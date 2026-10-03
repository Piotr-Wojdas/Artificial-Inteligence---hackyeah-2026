"""Pure functions on a Google Solar API buildingInsights response: roof segments and panel selection."""

from __future__ import annotations

import math

from .config import CONFIG, Config
from .errors import SolaryError

GOOGLE_PANEL_W = 400.0  # the panel Google lays out when the response does not say otherwise
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


class NoPanelsFit(SolaryError):
    pass


def compass(azimuth_deg: float) -> str:
    """Compass azimuth (0 = north, 90 = east) -> one of 8 directions."""
    return COMPASS[int(((azimuth_deg % 360) + 22.5) // 45) % 8]


def google_panel_watts(bi: dict) -> float:
    return float(bi["solarPotential"].get("panelCapacityWatts", GOOGLE_PANEL_W))


def panel_watts(bi: dict, cfg: Config = CONFIG) -> float:
    return float(cfg.panel_watts or google_panel_watts(bi))


def energy_scale(bi: dict, cfg: Config = CONFIG) -> float:
    """Google's yearly energy is for its own panel wattage; a panel of the same size with
    another wattage produces proportionally more or less."""
    return panel_watts(bi, cfg) / google_panel_watts(bi)


def ordered_panels(bi: dict, cfg: Config = CONFIG) -> list[dict]:
    """Every panel that fits on the roof, in the order they are used: the layout for N panels
    is the first N of this list (see `Config.panel_order`)."""
    panels = list(bi["solarPotential"].get("solarPanels", []))
    if cfg.panel_order == "yield":
        panels.sort(key=lambda p: -float(p.get("yearlyEnergyDcKwh", 0.0)))  # stable for ties
    elif cfg.panel_order != "google":
        raise SolaryError(f"unknown panel_order {cfg.panel_order!r}: use 'google' or 'yield'")
    return panels


def max_panels(bi: dict) -> int:
    return len(bi["solarPotential"].get("solarPanels", []))


def max_kwp(bi: dict, cfg: Config = CONFIG) -> float:
    return max_panels(bi) * panel_watts(bi, cfg) / 1000


def panels_for_kwp(bi: dict, kwp: float, cfg: Config = CONFIG) -> int:
    """Number of whole panels closest to `kwp` (half rounds up), between 1 and what the roof fits."""
    limit = max_panels(bi)
    if limit == 0:
        raise NoPanelsFit("no panel fits on this roof")
    n = math.floor(kwp * 1000 / panel_watts(bi, cfg) + 0.5)
    return min(max(n, 1), limit)


def segment_geometry(bi: dict, index: int) -> tuple[float, float]:
    """(pitch, compass azimuth) of a roof segment in degrees. Zero values may be omitted by the API."""
    s = bi["solarPotential"]["roofSegmentStats"][index]
    return float(s.get("pitchDegrees", 0.0)), float(s.get("azimuthDegrees", 0.0))


def segments_table(bi: dict) -> list[dict]:
    """One row per roof segment: geometry, area, how many panels fit and their mean DC yield."""
    sp = bi["solarPotential"]
    count: dict[int, int] = {}
    energy: dict[int, float] = {}
    for p in sp.get("solarPanels", []):
        i = int(p.get("segmentIndex", 0))
        count[i] = count.get(i, 0) + 1
        energy[i] = energy.get(i, 0.0) + float(p.get("yearlyEnergyDcKwh", 0.0))
    rows = []
    for i, s in enumerate(sp.get("roofSegmentStats", [])):
        pitch, az = segment_geometry(bi, i)
        n = count.get(i, 0)
        rows.append({"segment": i, "facing": compass(az), "pitch_deg": pitch, "azimuth_deg": az,
                     "area_m2": float(s.get("stats", {}).get("areaMeters2", 0.0)), "max_panels": n,
                     "kwh_dc_per_panel": energy[i] / n if n else None})
    return rows


def distance_to_building_m(lat: float, lon: float, bi: dict) -> float:
    """Distance from a point to the building's bounding box (0 when the point is on the building)."""
    sw, ne = bi["boundingBox"]["sw"], bi["boundingBox"]["ne"]
    dy = max(sw["latitude"] - lat, 0.0, lat - ne["latitude"]) * 111_320
    dx = max(sw["longitude"] - lon, 0.0, lon - ne["longitude"]) * 111_320 * math.cos(math.radians(lat))
    return math.hypot(dx, dy)
