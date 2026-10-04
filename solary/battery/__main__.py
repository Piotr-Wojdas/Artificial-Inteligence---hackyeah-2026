"""Command line for the battery controller.

    python -m solary.battery plan "Mariacka 1, Katowice" --battery-kwh 10 --annual-kwh 4000
    python -m solary.battery plan --lat 50.26 --lon 19.02 --kwp 6 --tariff dynamic
    python -m solary.battery evaluate          # all strategies on the test year -> evaluation.json
    python -m solary.battery train --steps 2000000   (needs: uv sync --group rl)

With an address the panels come from the roof analysis (python -m solary), so they are the ones
our layout chose; without roof data (or with --lat/--lon) from --kwp, --tilt and --azimuth.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from ..config import CONFIG
from ..errors import SolaryError
from .model import Battery, Tariff

STRATEGY_LABELS = {"no_battery": "bez magazynu", "rule": "zwykły falownik", "agent": "agent RL",
                   "mpc": "planowanie MPC", "optimum": "optimum (zna przyszłość)"}


def planes_from_roof(address: str | None, lat: float | None, lon: float | None, panels: int | None, cfg):
    """(lat, lon, planes, yearly kWh) of the panels chosen by the roof analysis, or None."""
    from ..service import analyze
    res = analyze(address=address, lat=lat, lon=lon, panels=panels, images=False, cfg=cfg)
    if not res["roof_available"]:
        return None
    s, b = res["selected"], res["building"]
    planes = [(r["kwp"], r["pitch_deg"], r["azimuth_deg"]) for r in s["per_segment"]]
    return b["lat"], b["lon"], planes, s["kwh_year"]


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m solary.battery", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="yearly savings and the battery plan for today and tomorrow")
    p.add_argument("address", nargs="?")
    p.add_argument("--lat", type=float)
    p.add_argument("--lon", type=float)
    p.add_argument("--panels", type=int, help="number of panels for the roof analysis")
    p.add_argument("--layout", choices=("google", "own"), help="panel layout for the roof analysis")
    p.add_argument("--kwp", type=float, default=6.0, help="PV size when there is no roof data (default 6)")
    p.add_argument("--tilt", type=float, default=35.0)
    p.add_argument("--azimuth", type=float, default=180.0, help="compass azimuth, 180 = south")
    p.add_argument("--battery-kwh", type=float, default=10.0)
    p.add_argument("--battery-kw", type=float, help="battery power (default: half the capacity per hour)")
    p.add_argument("--annual-kwh", type=float, default=4000.0, help="the household's yearly consumption")
    p.add_argument("--tariff", choices=("g11", "dynamic"), default="g11")
    p.add_argument("--soc", type=float, default=0.5, help="battery charge now, 0..1")
    p.add_argument("--no-plan", action="store_true", help="only the yearly savings")
    p.add_argument("--json", action="store_true")
    e = sub.add_parser("evaluate", help="all strategies on the test year (writes evaluation.json)")
    e.add_argument("--no-mpc", action="store_true", help="skip MPC (the slowest, about a minute per house)")
    t = sub.add_parser("train", help="train the agent with PPO (needs: uv sync --group rl)")
    t.add_argument("--steps", type=int, default=2_000_000, help="PPO steps after imitation (default 2,000,000)")
    t.add_argument("--imitation-weeks", type=int, default=320, help="MPC weeks to imitate first (0 = PPO alone)")
    t.add_argument("--out", type=Path)
    t.add_argument("--envs", type=int, default=8)
    t.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    try:
        if args.command == "train":
            from .policy import POLICY_PATH
            from .train import train
            train(args.steps, args.out or POLICY_PATH, n_envs=args.envs, seed=args.seed,
                  imitation_weeks=args.imitation_weeks)
        elif args.command == "evaluate":
            from .evaluate import evaluate
            evaluate(mpc=not args.no_mpc)
        else:
            return _plan(args)
    except SolaryError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


def _plan(args) -> int:
    from .plan import battery_report
    cfg = dataclasses.replace(CONFIG, layout=args.layout) if args.layout else CONFIG
    roof = None
    if args.address or (args.lat is not None and args.lon is not None and args.panels):
        roof = planes_from_roof(args.address, args.lat, args.lon, args.panels, cfg)
    if roof:
        lat, lon, planes, kwh_year = roof
    elif args.lat is not None and args.lon is not None:
        lat, lon, planes, kwh_year = args.lat, args.lon, [(args.kwp, args.tilt, args.azimuth)], None
    else:
        raise SolaryError("no roof data here: give --lat and --lon (and --kwp, --tilt, --azimuth)")
    battery = Battery(capacity_kwh=args.battery_kwh, power_kw=args.battery_kw or args.battery_kwh / 2)
    res = battery_report(lat, lon, planes, args.annual_kwh, battery, Tariff(kind=args.tariff), args.soc,
                         kwh_year, plan=not args.no_plan)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    y = res["year"]
    pv = ", ".join(f"{k:g} kWp {t:.0f}° az {a:.0f}°" for k, t, a in planes)
    print(f"House: {lat:.5f}, {lon:.5f} | PV {pv} | battery {battery.capacity_kwh:g} kWh / {battery.power_kw:g} kW | "
          f"{args.annual_kwh:,.0f} kWh a year | tariff {args.tariff}")
    print(f"\nTest year {y['period'][0]} .. {y['period'][1]}: PV {y['pv_kwh']:,} kWh, consumption {y['load_kwh']:,} kWh")
    print("  strategy                     bill a year   saved     of the optimum")
    for r in y["rows"]:
        share = f"{r['share_of_optimum']:.0%}" if r["share_of_optimum"] is not None else ""
        print(f"  {STRATEGY_LABELS.get(r['strategy'], r['strategy']):27s} {r['bill_zl']:9,.0f} zł {r['savings_zl']:7,.0f} zł  {share:>8}")
    plan = res["plan"]
    if plan and plan["steps"]:
        now = plan["now"]
        print(f"\nNow ({now['time'][11:16]}): {now['effect_label']} (the {plan['controller']} chose: {now['action_label']}); "
              f"prices published until {plan['prices_until']}")
        print(f"Plan to the end of tomorrow: {plan['cost_zl']:.2f} zł = bought {plan['buy_zl']:.2f} - sold "
              f"{plan['sell_zl']:.2f} + battery wear {plan['wear_zl']:.2f} (the usual inverter: {plan['rule_cost_zl']:.2f} zł)")
        print("  hour   RCE zł/kWh   PV kWh  use kWh  battery  action")
        for i in range(0, len(plan["steps"]), 4):
            hour = plan["steps"][i:i + 4]
            rce = [s["rce_zl_kwh"] for s in hour if s["rce_zl_kwh"] is not None]
            price = f"{sum(rce) / len(rce):6.2f}" if rce else "     –"
            print(f"  {hour[0]['time'][5:16].replace('T', ' ')}  {price}"
                  f"  {sum(s['pv_kwh'] for s in hour):7.2f}  {sum(s['load_kwh'] for s in hour):7.2f}"
                  f"  {hour[-1]['soc']:6.0%}   {hour[0]['effect_label']}")
    print(f"\n{res['attribution']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
