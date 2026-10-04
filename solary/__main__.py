"""Command line: python -m solary "Mariacka 1, Katowice" --kwp 6

Prints the roof found, the panels chosen and their yearly and monthly production. With images
(default) it also saves three PNGs: "is this your house?", all panels, the chosen panels.
--layout own places the panels with our algorithm (solary/layout.py) instead of Google's.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from .config import CONFIG
from .errors import SolaryError
from .service import analyze

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WARNINGS = {
    "street_only": "only the street was matched (no house number): the building found may be a random one",
    "far_from_address": "the building found is far from the searched point: it may be a neighbour's, "
                        "check the 'confirm' image",
    "monthly_fallback": "PVGIS unreachable: months follow a typical profile for Poland",
    "images_unavailable": "the map layers could not be downloaded, no images",
    "layout_fallback": "the height map is unavailable: Google's layout is shown instead of ours",
}
REASONS = {
    "no_roof_data": "Google Solar API has no roof data for this location",
    "no_panels_fit": "no panel fits on the roof found here",
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m solary", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("address", nargs="?", help='street address, e.g. "Mariacka 1, Katowice"')
    ap.add_argument("--lat", type=float, help="latitude of the roof (instead of an address)")
    ap.add_argument("--lon", type=float, help="longitude of the roof (instead of an address)")
    ap.add_argument("--kwp", type=float, help=f"installation size (default {CONFIG.default_kwp:g} kWp)")
    ap.add_argument("--panels", type=int, help="number of panels (instead of --kwp)")
    ap.add_argument("--panel-watts", type=float, help="wattage of one panel (default: Google's 400 W)")
    ap.add_argument("--order", choices=("google", "yield"), help="which panels are used first: the layout's own "
                    "order (default, compact arrays) or strictly the highest yield")
    ap.add_argument("--layout", choices=("google", "own", "pro"), help="who places the panels: Google (default), our "
                    "classic algorithm (own), or our professional aesthetic matrix algorithm (pro)")
    ap.add_argument("--margin", type=float, help=f"own layout: free roof kept around every panel, in metres "
                    f"(default {CONFIG.layout_margin_m:g})")
    ap.add_argument("--gap", type=float, help=f"own layout: gap between panels, in metres "
                    f"(default {CONFIG.layout_gap_m:g})")
    ap.add_argument("--panel-size", type=float, nargs=2, metavar=("HEIGHT", "WIDTH"), help="own layout: panel size "
                    "in metres (default: Google's 1.879 x 1.045); set --panel-watts to match")
    ap.add_argument("--tilt", type=float, help="tilt in degrees, used only when there is no roof data")
    ap.add_argument("--azimuth", type=float, help="compass azimuth (180 = south), used only when there is no roof data")
    ap.add_argument("--no-images", action="store_true", help="skip the map layers and PNG previews")
    ap.add_argument("--data", type=Path, help="cache folder (default: ./data or $SOLARY_DATA)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = CONFIG
    if args.data:
        cfg = dataclasses.replace(cfg, data_dir=args.data)
    if args.panel_watts:
        cfg = dataclasses.replace(cfg, panel_watts=args.panel_watts)
    if args.order:
        cfg = dataclasses.replace(cfg, panel_order=args.order)
    if args.layout:
        cfg = dataclasses.replace(cfg, layout=args.layout)
    if args.margin is not None:
        cfg = dataclasses.replace(cfg, layout_margin_m=args.margin)
    if args.gap is not None:
        cfg = dataclasses.replace(cfg, layout_gap_m=args.gap)
    if args.panel_size:
        cfg = dataclasses.replace(cfg, panel_size_m=tuple(args.panel_size))
    try:
        res = analyze(address=args.address, lat=args.lat, lon=args.lon, kwp=args.kwp, panels=args.panels,
                      images=not args.no_images, tilt=args.tilt, azimuth=args.azimuth, cfg=cfg)
    except SolaryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    q = res["query"]
    print(f"Point: {q['lat']:.5f}, {q['lon']:.5f}" + (f"  ({res['address_label']})" if res["address_label"] else ""))
    for alt in res["alternatives"]:
        print(f"  other match: {alt['lat']:.5f}, {alt['lon']:.5f}  ({alt['label']})")
    for w in res["warnings"]:
        print(f"WARNING: {WARNINGS.get(w, w)}")
    if res["roof_available"]:
        _print_roof(res, cfg.roof_dir)
    else:
        _print_generic(res)
    print(f"\n{res['attribution']}")
    return 0


def _print_months(monthly: list[float]) -> None:
    print("  " + "  ".join(f"{m:>5}" for m in MONTHS))
    print("  " + "  ".join(f"{v:5.0f}" for v in monthly))


def _print_roof(res: dict, roof_dir: Path) -> None:
    b, s = res["building"], res["selected"]
    print(f"Building: {b['lat']:.5f}, {b['lon']:.5f}, {b['distance_m']:.0f} m from the point | imagery "
          f"{b['imagery_quality']} {b['imagery_date']} | roof {b['roof_area_m2']:.0f} m2 | "
          f"fits {b['max_panels']} panels = {b['max_kwp']:.1f} kWp")
    print("\nRoof segments:")
    print("  seg  facing  pitch  azimuth  area m2  panels  kWh DC/panel")
    for r in res["segments"]:
        per_panel = f"{r['kwh_dc_per_panel']:12.0f}" if r["kwh_dc_per_panel"] is not None else f"{'-':>12}"
        print(f"  {r['segment']:3d}  {r['facing']:>6}  {r['pitch_deg']:5.1f}  {r['azimuth_deg']:7.1f}  "
              f"{r['area_m2']:7.1f}  {r['max_panels']:6d}  {per_panel}")
    print(f"\nChosen: {s['panels']} panels x {res['panel']['watts']:g} W = {s['kwp']:.1f} kWp -> "
          f"{s['kwh_year']:,.0f} kWh a year ({s['kwh_per_kwp']:,.0f} kWh/kWp)")
    if s["pvgis_kwh_year"]:
        print(f"  PVGIS for the same panels without local shade: {s['pvgis_kwh_year']:,.0f} kWh a year")
    lay = res["layout"]
    if lay["algorithm"] == "own":
        theirs = (f"{lay['google_kwh_year']:,.0f} kWh a year" if lay["google_kwh_year"] is not None
                  else "none, it fits fewer")
        print(f"  Layout: ours, {lay['margin_m']:g} m margin and {lay['gap_m']:g} m gap. Google's layout for the "
              f"same number of panels: {theirs} (Google fits {lay['google_max_panels']} panels at most)")
    print(f"Monthly kWh ({s['monthly_source']}):")
    _print_months(s["monthly_kwh"])
    print("Per segment:")
    for r in s["per_segment"]:
        ref = f", PVGIS {r['pvgis_kwh_per_kwp']:,.0f}" if r["pvgis_kwh_per_kwp"] else ""
        print(f"  seg {r['segment']}: {r['facing']} {r['pitch_deg']:.0f} deg, {r['panels']} panels, "
              f"{r['kwh_year']:,.0f} kWh a year ({r['kwh_per_kwp']:,.0f} kWh/kWp{ref})")
    print("\nOther sizes:")
    for r in res["sizes"]:
        print(f"  {r['kwp']:6.1f} kWp = {r['panels']:3d} panels: {r['kwh_year']:8,.0f} kWh a year "
              f"({r['kwh_per_kwp']:,.0f} kWh/kWp)")
    for name, file in res["images"].items():
        print(f"image '{name}': {roof_dir / file}")


def _print_generic(res: dict) -> None:
    g = res["generic"]
    print(f"No panel layout: {REASONS.get(res['reason'], res['reason'])}.")
    print(f"PVGIS-only estimate for {g['kwp']:g} kWp at {g['tilt_deg']:g} deg facing {g['facing']}: "
          f"{g['kwh_year']:,.0f} kWh a year ({g['kwh_per_kwp']:,.0f} kWh/kWp)")
    print(f"Monthly kWh ({g['monthly_source']}):")
    _print_months(g["monthly_kwh"])


if __name__ == "__main__":
    sys.exit(main())
