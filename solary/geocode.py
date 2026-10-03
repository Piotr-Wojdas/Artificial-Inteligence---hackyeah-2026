"""Address -> coordinates with OpenStreetMap Nominatim (no API key).

Usage policy of the public server: at most 1 request per second, a User-Agent that identifies
the application, results cached. Attribution: "(c) OpenStreetMap contributors".
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time

import requests

from .config import CONFIG, Config
from .errors import SolaryError

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_INTERVAL_S = 1.1
MAX_HITS = 5
SAME_PLACE_M = 30.0  # hits closer than this to an earlier one are the same building
# Polish addresses are usually written "ul. Mariacka 1"; OpenStreetMap stores the street as
# "Mariacka" and Nominatim finds nothing with the prefix. ("al.", "pl.", "os." are understood.)
STREET_PREFIX = re.compile(r"(?<!\w)(?:ulica\s+|ul\.\s*|ul\s+)", re.IGNORECASE)

_lock = threading.Lock()
_last_call = 0.0


class AddressNotFound(SolaryError):
    pass


class GeocodingError(SolaryError):
    pass


def user_agent() -> str:
    """Nominatim wants to know who is calling: set SOLARY_USER_AGENT (app name + contact)."""
    return os.environ.get("SOLARY_USER_AGENT", "solary/0.1 (rooftop PV estimator)")


def normalise(address: str) -> str:
    """Collapse whitespace and drop the "ul." / "ulica" street prefix."""
    return " ".join(STREET_PREFIX.sub("", address).split()).strip(" ,")


def _distance_m(a: dict, b: dict) -> float:
    dy = (a["lat"] - b["lat"]) * 111_320
    dx = (a["lon"] - b["lon"]) * 111_320 * math.cos(math.radians(a["lat"]))
    return math.hypot(dx, dy)


def geocode(address: str, cfg: Config = CONFIG) -> dict:
    """Best match of `address`: {"lat", "lon", "label", "house_level", "alternatives"}.

    `house_level` is False when only the street (or a larger area) was matched, i.e. the point
    is not a specific building and the roof found near it may be a random one.
    `alternatives` are the other places Nominatim returned for the same text (same fields
    without "alternatives"), for a "did you mean" list.
    """
    query = normalise(address)
    if not query:
        raise AddressNotFound("empty address")
    key = f"{cfg.country_codes or '*'}|{cfg.language}|{query.lower()}"
    path = cfg.data_dir / "geocode.json"
    with _lock:
        cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if key in cache:
            return cache[key]

        global _last_call
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        params = {"q": query, "format": "jsonv2", "limit": MAX_HITS}
        if cfg.country_codes:
            params["countrycodes"] = cfg.country_codes
        try:
            r = requests.get(NOMINATIM_URL, params=params, timeout=30,
                             headers={"User-Agent": user_agent(), "Accept-Language": cfg.language})
            _last_call = time.monotonic()
            r.raise_for_status()
            hits = r.json()
        except (requests.RequestException, ValueError) as e:
            raise GeocodingError(f"address search (OpenStreetMap Nominatim) failed: {type(e).__name__}") from e
        if not hits:
            raise AddressNotFound(f"address not found: {address!r} (add the city, or pass coordinates)")

        places: list[dict] = []
        for hit in hits:
            place = {"lat": float(hit["lat"]), "lon": float(hit["lon"]), "label": hit.get("display_name", query),
                     "house_level": int(hit.get("place_rank", 30)) >= cfg.min_house_rank}
            if all(_distance_m(place, p) > SAME_PLACE_M for p in places):
                places.append(place)
        res = places[0] | {"alternatives": places[1:]}
        cache[key] = res
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        return res
