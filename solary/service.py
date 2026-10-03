"""The whole feature in one call: address -> building -> panel layout -> production estimate.

`analyze()` is what the CLI (`python -m solary`) and the HTTP API (`solary/api.py`) both use.
"""

from __future__ import annotations

import dataclasses

from .config import CONFIG, Config
from .errors import SolaryError
from .geocode import geocode
from .layout import LayoutUnavailable, layout_key, with_own_layout
from .panels import (distance_to_building_m, energy_scale, google_panel_watts, max_kwp, max_panels, ordered_panels,
                     panel_watts, panels_for_kwp, segments_table)
from .production import estimate_generic, estimate_roof, sizes_table
from .render import PANEL_H_M, PANEL_W_M, render
from .solar_api import (LAYERS, LAYOUT_LAYERS, RoofNotFound, SolarApiError, building_insights, data_layers,
                        purge_expired, tag)

ATTRIBUTION = ("Roof data and imagery © Google (Solar API); address search © OpenStreetMap contributors; "
               "monthly profile: PVGIS © European Union")
ATTRIBUTION_GENERIC = "Address search © OpenStreetMap contributors; production estimate: PVGIS © European Union"


def analyze(address: str | None = None, lat: float | None = None, lon: float | None = None,
            kwp: float | None = None, panels: int | None = None, images: bool = True,
            tilt: float | None = None, azimuth: float | None = None, cfg: Config = CONFIG) -> dict:
    """Roof of the building at `address` (or at lat/lon) with a layout of `panels` panels (or the
    whole panels closest to `kwp`; default cfg.default_kwp) and their production.

    Where Google has no roof data the result has "roof_available": False and a PVGIS-only
    estimate for `kwp` at `tilt`/`azimuth` (compass degrees, 180 = south; defaults in cfg).
    `images=True` also renders the PNG previews and returns their file names (in cfg.roof_dir).
    `cfg.layout` chooses who places the panels: Google ("google") or our algorithm ("own",
    solary/layout.py); when the map layers for ours are unavailable Google's layout is used and
    the result has the warning "layout_fallback".
    """
    purge_expired(cfg)
    label, house_level, alternatives = None, True, []
    if address and address.strip():
        g = geocode(address, cfg)
        lat, lon, label = g["lat"], g["lon"], g["label"]
        house_level, alternatives = g["house_level"], g["alternatives"]
    elif lat is None or lon is None:
        raise SolaryError("give an address, or both lat and lon")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise SolaryError("lat must be within -90..90 and lon within -180..180")
    if kwp is not None and kwp <= 0:
        raise SolaryError("kwp must be greater than 0")
    if panels is not None and panels < 1:
        raise SolaryError("panels must be at least 1")
    if cfg.layout not in ("google", "own"):
        raise SolaryError(f"unknown layout {cfg.layout!r}: use 'google' or 'own'")

    warnings = [] if house_level else ["street_only"]
    base = {"query": {"address": address, "lat": lat, "lon": lon}, "address_label": label,
            "house_level": house_level, "alternatives": alternatives, "assumptions": cfg.public(),
            "attribution": ATTRIBUTION}
    try:
        google = bi = building_insights(lat, lon, cfg)
    except RoofNotFound:
        return base | _generic(lat, lon, kwp, tilt, azimuth, "no_roof_data", warnings, cfg)
    if cfg.layout == "own":
        try:
            bi = _own_layout(google, images, cfg)
        except (SolarApiError, LayoutUnavailable):   # no height map here: Google's own layout instead
            warnings.append("layout_fallback")
            cfg = dataclasses.replace(cfg, layout="google")
            base["assumptions"] = cfg.public()
    if not max_panels(bi):
        return base | _generic(lat, lon, kwp, tilt, azimuth, "no_panels_fit", warnings, cfg)

    n = min(panels, max_panels(bi)) if panels is not None else panels_for_kwp(bi, kwp or cfg.default_kwp, cfg)
    selected = estimate_roof(bi, n, cfg)
    if selected["monthly_source"] != "pvgis":
        warnings.append("monthly_fallback")
    distance = distance_to_building_m(lat, lon, bi)
    if distance > cfg.warn_distance_m:
        warnings.append("far_from_address")

    files: dict[str, str] = {}
    if images:
        try:
            files = _render_all(bi, lat, lon, n, cfg)
        except SolarApiError:
            warnings.append("images_unavailable")

    sp, centre, date = bi["solarPotential"], bi["center"], bi.get("imageryDate", {})
    return base | {
        "roof_available": True,
        "warnings": warnings,
        "building": {
            "lat": centre["latitude"], "lon": centre["longitude"], "distance_m": distance,
            "roof_area_m2": float(sp.get("wholeRoofStats", {}).get("areaMeters2", 0.0)),
            "max_panels": max_panels(bi), "max_kwp": max_kwp(bi, cfg),
            "imagery_quality": bi.get("imageryQuality"),
            "imagery_date": f"{date['year']}-{date.get('month', 1):02d}" if date.get("year") else None,
        },
        "panel": {"watts": panel_watts(bi, cfg), "google_watts": google_panel_watts(bi),
                  "height_m": float(sp.get("panelHeightMeters", PANEL_H_M)),
                  "width_m": float(sp.get("panelWidthMeters", PANEL_W_M))},
        "segments": segments_table(bi),
        "selected": selected,
        "sizes": sizes_table(bi, cfg),
        "images": files,
        "layout": _layout_info(google, n, cfg),
    }


def _own_layout(bi: dict, images: bool, cfg: Config) -> dict:
    """The building with our panel layout instead of Google's. The layers the previews need are
    fetched in the same request, so a new building still costs one dataLayers call."""
    names = LAYOUT_LAYERS + tuple(n for n in LAYERS if images and n not in LAYOUT_LAYERS)
    layers = data_layers(bi, cfg, names)
    return with_own_layout(bi, {n: layers[n] for n in LAYOUT_LAYERS}, cfg)


def _layout_info(google: dict, n: int, cfg: Config) -> dict:
    """Which algorithm placed the panels and, for ours, what Google's layout gives for the same
    number of panels: the cross-check shown next to the result."""
    if cfg.layout != "own":
        return {"algorithm": "google"}
    theirs = ordered_panels(google, cfg)
    kwh = sum(float(p.get("yearlyEnergyDcKwh", 0.0)) for p in theirs[:n]) * energy_scale(google, cfg) * cfg.ac_factor
    return {"algorithm": "own", "margin_m": cfg.layout_margin_m, "gap_m": cfg.layout_gap_m,
            "google_max_panels": len(theirs), "google_kwh_year": kwh if 0 < n <= len(theirs) else None}


def _generic(lat: float, lon: float, kwp: float | None, tilt: float | None, azimuth: float | None,
             reason: str, warnings: list[str], cfg: Config) -> dict:
    tilt = cfg.generic_tilt_deg if tilt is None else tilt
    azimuth = cfg.generic_azimuth_deg if azimuth is None else azimuth
    if not 0 <= tilt <= 90:
        raise SolaryError("tilt must be within 0..90 degrees")
    kwp = kwp or cfg.default_kwp
    generic = estimate_generic(lat, lon, kwp, tilt, azimuth, cfg)
    if generic["monthly_source"] != "pvgis":
        warnings = warnings + ["monthly_fallback"]
    sizes = [{"kwp": k, "kwh_year": generic["kwh_per_kwp"] * k, "kwh_per_kwp": generic["kwh_per_kwp"]}
             for k in cfg.sizes_kwp]
    return {"roof_available": False, "reason": reason, "warnings": warnings, "generic": generic,
            "sizes": sizes, "images": {}, "attribution": ATTRIBUTION_GENERIC}


def _render_all(bi: dict, lat: float, lon: float, n: int, cfg: Config) -> dict[str, str]:
    layers = data_layers(bi, cfg)
    centre = bi["center"]
    building = tag(centre["latitude"], centre["longitude"])
    panels = ordered_panels(bi, cfg)
    # our layout depends on its settings, so their id is part of the file names
    own = f"own-{layout_key(cfg)}" if cfg.layout == "own" else ""
    order = own + ("-yield" if cfg.panel_order == "yield" else "") if own else cfg.panel_order
    paths = {
        "confirm": render(bi, layers, cfg.roof_dir / f"confirm_{building}_{tag(lat, lon)}.png", [],
                          flux_alpha=0.0, mark=(lat, lon), outline=True),
        "all": render(bi, layers, cfg.roof_dir / f"panels_{building}_{own + '_' if own else ''}all.png", panels),
        "selected": render(bi, layers, cfg.roof_dir / f"panels_{building}_{order}_{n}.png", panels[:n]),
    }
    return {k: p.name for k, p in paths.items()}
