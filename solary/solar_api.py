"""Google Solar API client: roof geometry + panel layout (buildingInsights) and map layers (dataLayers).

buildingInsights returns, for the building closest to a coordinate:
  * roof segments - pitch, azimuth, area;
  * every panel that fits (400 W, 1.879 x 1.045 m) with its position, orientation and yearly DC
    energy, already including shade from trees, neighbouring buildings and the roof itself.
dataLayers adds GeoTIFFs around the building: aerial RGB, building mask, annual solar flux.

The API key is read from the environment, which `solary/env.py` fills from a `.env` file:
GOOGLE_MAPS_API_KEY=...   (GOOGLE_SOLAR_API_KEY and solar_api are accepted too).
The key never appears in error messages. Cached responses are deleted after `cfg.cache_days`
(Solar API terms: at most 30 days).
"""

from __future__ import annotations

import json
import math
import os
import shutil
import time
from pathlib import Path

import requests

from .config import CONFIG, Config
from .errors import SolaryError

SOLAR_URL = "https://solar.googleapis.com/v1"
KEY_NAMES = ("GOOGLE_MAPS_API_KEY", "GOOGLE_SOLAR_API_KEY", "solar_api")
LAYERS = ("rgb", "mask", "annualFlux")


class MissingApiKey(SolaryError):
    pass


class RoofNotFound(SolaryError):
    """Google has no roof data for this location (outside its coverage)."""


class SolarApiError(SolaryError):
    pass


# ---------------------------------------------------------------- API key
def api_key() -> str:
    for name in KEY_NAMES:
        if os.environ.get(name):
            return os.environ[name]
    raise MissingApiKey("Google Solar API key not found: put GOOGLE_MAPS_API_KEY=... in the .env file")


def has_api_key() -> bool:
    try:
        api_key()
        return True
    except MissingApiKey:
        return False


# ---------------------------------------------------------------- cache
def tag(lat: float, lon: float) -> str:
    return f"{lat:.5f}_{lon:.5f}"


def _fresh(path: Path, cfg: Config) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < cfg.cache_days * 86400


def purge_expired(cfg: Config = CONFIG) -> int:
    """Delete cached Google data (responses, map layers, rendered images) older than cfg.cache_days."""
    removed = 0
    if not cfg.roof_dir.is_dir():
        return removed
    limit = time.time() - cfg.cache_days * 86400
    for p in cfg.roof_dir.iterdir():
        if p.stat().st_mtime >= limit:
            continue
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
        removed += 1
    return removed


# ---------------------------------------------------------------- requests
def _get(url: str, params: dict, timeout: float) -> requests.Response:
    """GET with the API key added; network errors are re-raised without the URL (it holds the key)."""
    try:
        return requests.get(url, params={**params, "key": api_key()}, timeout=timeout)
    except requests.RequestException as e:
        raise SolarApiError(f"Google Solar API unreachable: {type(e).__name__}") from None


def _error_message(r: requests.Response) -> str:
    try:
        return str(r.json().get("error", {}).get("message", "")) or r.reason
    except ValueError:
        return r.reason


def building_insights(lat: float, lon: float, cfg: Config = CONFIG) -> dict:
    """Roof and panel layout of the building closest to (lat, lon), best imagery available."""
    path = cfg.roof_dir / f"building_{tag(lat, lon)}.json"
    if _fresh(path, cfg):
        return json.loads(path.read_text(encoding="utf-8"))
    for quality in cfg.qualities:
        r = _get(f"{SOLAR_URL}/buildingInsights:findClosest", timeout=60, params={
            "location.latitude": lat, "location.longitude": lon, "requiredQuality": quality})
        if r.ok:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(r.json()), encoding="utf-8")
            return r.json()
        if r.status_code != 404:  # 404 = no data at this quality -> try the next one
            raise SolarApiError(f"Google Solar API error {r.status_code}: {_error_message(r)}")
    raise RoofNotFound("Google Solar API has no roof data for this location")


def data_layers(bi: dict, cfg: Config = CONFIG) -> dict[str, Path]:
    """Download the aerial image, building mask and annual flux GeoTIFFs around the building."""
    c = bi["center"]
    out_dir = cfg.roof_dir / f"layers_{tag(c['latitude'], c['longitude'])}"
    paths = {n: out_dir / f"{n}.tif" for n in LAYERS}
    if all(_fresh(p, cfg) for p in paths.values()):
        return paths
    sw, ne = bi["boundingBox"]["sw"], bi["boundingBox"]["ne"]
    dy = (ne["latitude"] - sw["latitude"]) * 111_000
    dx = (ne["longitude"] - sw["longitude"]) * 111_000 * math.cos(math.radians(c["latitude"]))
    radius_m = min(max(math.hypot(dx, dy) / 2 + 8, 20.0), 100.0)  # the building plus a margin
    r = _get(f"{SOLAR_URL}/dataLayers:get", timeout=60, params={
        "location.latitude": c["latitude"], "location.longitude": c["longitude"], "radiusMeters": radius_m,
        "view": "IMAGERY_AND_ANNUAL_FLUX_LAYERS", "requiredQuality": bi.get("imageryQuality", "HIGH"),
        "pixelSizeMeters": 0.1})
    if not r.ok:
        raise SolarApiError(f"Google Solar API dataLayers error {r.status_code}: {_error_message(r)}")
    urls = r.json()
    out_dir.mkdir(parents=True, exist_ok=True)
    for n in LAYERS:
        g = _get(urls[f"{n}Url"], params={}, timeout=120)
        if not g.ok:
            raise SolarApiError(f"Google Solar API layer '{n}' error {g.status_code}")
        paths[n].write_bytes(g.content)
    return paths
