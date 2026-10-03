"""How much electricity the chosen panels produce: yearly and month by month.

Roof found (Google Solar API):
  yearly AC energy = sum of the chosen panels' yearly DC energy from Google (location-specific,
                     includes shade from trees, neighbours and the roof itself)
                     x (1 - system losses) x (panel wattage / Google's panel wattage).
  Months: the yearly figure is split with the monthly profile PVGIS gives for the roof's own
  coordinates and each segment's tilt and azimuth. The same PVGIS call gives an independent,
  shade-free yield for that orientation, reported next to ours as a cross-check.

No roof data (address outside Google's coverage): PVGIS alone, for an assumed tilt and azimuth.

PVGIS (JRC, European Commission) is free and needs no key. When it cannot be reached, months
follow a typical profile for Poland and the result says so (`monthly_source`).
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import CONFIG, Config
from .panels import compass, energy_scale, ordered_panels, panel_watts, panels_for_kwp, segment_geometry

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"

# Used only when PVGIS is unreachable. Source: PVGIS 5.3 (SARAH3, 2005-2023) for central Poland
# (Lodz, 51.76 N 19.46 E), 1 kWp, 35 deg, south, roof-mounted, 14 % losses: 1,023 kWh/kWp a year.
FALLBACK_MONTHLY_SHARE_PL = (0.0326, 0.0478, 0.0862, 0.1129, 0.1236, 0.1261,
                             0.1231, 0.1177, 0.0990, 0.0702, 0.0345, 0.0264)
FALLBACK_KWH_PER_KWP_PL = 1000.0

_lock = threading.Lock()


def pvgis_aspect(azimuth_deg: float) -> float:
    """Compass azimuth (0 = north, 90 = east, 180 = south) -> PVGIS aspect (0 = south, 90 = west, -90 = east)."""
    return (azimuth_deg % 360) - 180


def _orientation(pitch_deg: float, azimuth_deg: float) -> tuple[int, int]:
    """Whole degrees as sent to PVGIS: (tilt 0..90, aspect -180..180)."""
    return min(max(round(pitch_deg), 0), 90), round(pvgis_aspect(azimuth_deg))


def _normalised(values) -> list[float]:
    total = float(sum(values))
    return [float(v) / total for v in values]


def _cache_key(lat: float, lon: float, tilt: int, aspect: int, cfg: Config) -> str:
    return f"{lat:.2f},{lon:.2f},{tilt},{aspect},{cfg.system_loss_pct:g},{cfg.pvgis_mounting}"


def _fetch(lat: float, lon: float, tilt: int, aspect: int, cfg: Config) -> dict | None:
    """PVGIS for 1 kWp: yearly kWh and 12 monthly kWh; None when PVGIS cannot answer."""
    try:
        r = requests.get(PVGIS_URL, timeout=60, params={
            "lat": round(lat, 2), "lon": round(lon, 2), "peakpower": 1, "loss": cfg.system_loss_pct,
            "angle": tilt, "aspect": aspect, "mountingplace": cfg.pvgis_mounting, "outputformat": "json"})
        r.raise_for_status()
        out = r.json()["outputs"]
        monthly = [float(m["E_m"]) for m in sorted(out["monthly"]["fixed"], key=lambda m: m["month"])]
        yearly = float(out["totals"]["fixed"]["E_y"])
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None
    if len(monthly) != 12 or min(monthly) < 0 or sum(monthly) <= 0 or yearly <= 0:
        return None
    return {"kwh_per_kwp_year": yearly, "monthly_kwh_per_kwp": monthly}


def pvgis_lookup(lat: float, lon: float, orientations: set[tuple[int, int]], cfg: Config = CONFIG) -> dict:
    """{(tilt, aspect): PVGIS result or None} for each orientation at this location (cached on disk)."""
    path = cfg.data_dir / "pvgis_cache.json"

    def load() -> dict:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    with _lock:
        cache = load()
    missing = [o for o in sorted(orientations) if _cache_key(lat, lon, *o, cfg) not in cache]
    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda o: _fetch(lat, lon, *o, cfg), missing))
        with _lock:
            cache = load()
            for o, res in zip(missing, results):
                if res is not None:
                    cache[_cache_key(lat, lon, *o, cfg)] = res
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache), encoding="utf-8")
    return {o: cache.get(_cache_key(lat, lon, *o, cfg)) for o in orientations}


def estimate_roof(bi: dict, n_panels: int, cfg: Config = CONFIG) -> dict:
    """Production of the first `n_panels` panels of this roof's layout."""
    chosen = ordered_panels(bi, cfg)[:n_panels]
    watts, scale = panel_watts(bi, cfg), energy_scale(bi, cfg)
    by_segment: dict[int, list[dict]] = {}
    for p in chosen:
        by_segment.setdefault(int(p.get("segmentIndex", 0)), []).append(p)
    geometry = {i: segment_geometry(bi, i) for i in by_segment}
    orientation = {i: _orientation(*geometry[i]) for i in by_segment}
    centre = bi["center"]
    pvgis = pvgis_lookup(centre["latitude"], centre["longitude"], set(orientation.values()), cfg)

    monthly = [0.0] * 12
    rows = []
    for i in sorted(by_segment):
        group = by_segment[i]
        pitch, azimuth = geometry[i]
        kwh_year = sum(float(p.get("yearlyEnergyDcKwh", 0.0)) for p in group) * scale * cfg.ac_factor
        kwp = len(group) * watts / 1000
        ref = pvgis[orientation[i]]
        share = _normalised(ref["monthly_kwh_per_kwp"] if ref else FALLBACK_MONTHLY_SHARE_PL)
        for m in range(12):
            monthly[m] += kwh_year * share[m]
        rows.append({
            "segment": i, "facing": compass(azimuth), "pitch_deg": pitch, "azimuth_deg": azimuth,
            "panels": len(group), "kwp": kwp, "kwh_year": kwh_year, "kwh_per_kwp": kwh_year / kwp,
            # independent estimate for the same tilt and azimuth without local shade
            "pvgis_kwh_per_kwp": ref["kwh_per_kwp_year"] if ref else None,
            "vs_pvgis": kwh_year / kwp / ref["kwh_per_kwp_year"] if ref else None,
        })
    kwp = len(chosen) * watts / 1000
    kwh_year = sum(r["kwh_year"] for r in rows)
    pvgis_ok = all(r["pvgis_kwh_per_kwp"] is not None for r in rows)
    return {
        "panels": len(chosen), "kwp": kwp, "kwh_year": kwh_year, "kwh_per_kwp": kwh_year / kwp,
        "monthly_kwh": monthly,
        "monthly_source": "pvgis" if pvgis_ok else "typical_poland",
        # the same panels according to PVGIS alone (no local shade)
        "pvgis_kwh_year": sum(r["pvgis_kwh_per_kwp"] * r["kwp"] for r in rows) if pvgis_ok else None,
        "per_segment": rows,
    }


def sizes_table(bi: dict, cfg: Config = CONFIG) -> list[dict]:
    """Yearly production for the standard sizes that fit, and for the full roof."""
    panels = ordered_panels(bi, cfg)
    if not panels:
        return []
    watts, factor = panel_watts(bi, cfg), energy_scale(bi, cfg) * cfg.ac_factor
    limit_kwp = len(panels) * watts / 1000
    counts = {panels_for_kwp(bi, k, cfg) for k in cfg.sizes_kwp if k <= limit_kwp} | {len(panels)}
    cumulative, total = [], 0.0
    for p in panels:
        total += float(p.get("yearlyEnergyDcKwh", 0.0))
        cumulative.append(total)
    return [{"panels": n, "kwp": n * watts / 1000, "kwh_year": cumulative[n - 1] * factor,
             "kwh_per_kwp": cumulative[n - 1] * factor / (n * watts / 1000)} for n in sorted(counts)]


def estimate_generic(lat: float, lon: float, kwp: float, tilt_deg: float, azimuth_deg: float,
                     cfg: Config = CONFIG) -> dict:
    """Production of `kwp` at this location from PVGIS alone (no roof data, no local shade)."""
    o = _orientation(tilt_deg, azimuth_deg)
    ref = pvgis_lookup(lat, lon, {o}, cfg)[o]
    per_kwp = ref["kwh_per_kwp_year"] if ref else FALLBACK_KWH_PER_KWP_PL
    share = _normalised(ref["monthly_kwh_per_kwp"] if ref else FALLBACK_MONTHLY_SHARE_PL)
    return {
        "kwp": kwp, "kwh_year": per_kwp * kwp, "kwh_per_kwp": per_kwp,
        "monthly_kwh": [per_kwp * kwp * s for s in share],
        "monthly_source": "pvgis" if ref else "typical_poland",
        "tilt_deg": tilt_deg, "azimuth_deg": azimuth_deg, "facing": compass(azimuth_deg),
    }
