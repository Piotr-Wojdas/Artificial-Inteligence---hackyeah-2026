"""Every strategy on the test year (train.TEST), which the agent has never seen: the bills settled
by the net-billing rules, and how much of the possible savings each strategy gets.

    uv run python -m solary.battery evaluate          # writes solary/battery/evaluation.json
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import numpy as np

from ..config import CONFIG, Config
from . import strategies as S
from .data import Inputs, history, load_profile, local_day_bounds
from .model import Battery, Scenario, Tariff
from .policy import POLICY_PATH, Policy
from .train import LOCATIONS, TEST

REPORT_PATH = Path(__file__).with_name("evaluation.json")
REFERENCE = [
    {"name": "Dom 6 kWp na południe, magazyn 10 kWh, G11", "planes": [(6.0, 35, 180)], "annual_kwh": 4000,
     "battery": Battery(10, 5), "tariff": Tariff("g11")},
    {"name": "Dom 6 kWp na południe, magazyn 10 kWh, taryfa dynamiczna", "planes": [(6.0, 35, 180)],
     "annual_kwh": 4000, "battery": Battery(10, 5), "tariff": Tariff("dynamic")},
    {"name": "Dom 8 kWp wschód-zachód, magazyn 5 kWh, G11", "planes": [(4.0, 30, 90), (4.0, 30, 270)],
     "annual_kwh": 5000, "battery": Battery(5, 3), "tariff": Tariff("g11")},
    {"name": "Dom 4 kWp na południe, magazyn 15 kWh, taryfa dynamiczna", "planes": [(4.0, 35, 180)],
     "annual_kwh": 6000, "battery": Battery(15, 7.5), "tariff": Tariff("dynamic")},
]


def test_inputs(lat: float, lon: float, cfg: Config = CONFIG) -> Inputs:
    """Prices and weather for the test year plus a day before (forecasts) and the horizon after."""
    return history(lat, lon, TEST[0] - timedelta(days=1), TEST[1], cfg)


def scenario(inputs: Inputs, planes, annual_kwh: float, battery: Battery, tariff: Tariff,
             pv_kwh_year: float | None = None, seed: int = 0) -> Scenario:
    """The house over the test year. `pv_kwh_year` rescales the PV to a known yearly production
    (e.g. the roof analysis, which includes local shade)."""
    pv = inputs.pv(list(planes))
    start = int(inputs.index.get_loc(local_day_bounds(TEST[0], TEST[0])[0]))
    if pv_kwh_year:
        pv = pv * pv_kwh_year / max(float(pv[start:].sum()), 1e-9)
    load, expected = load_profile(inputs.index, annual_kwh, seed=seed)
    return Scenario(index=inputs.index, rce=inputs.rce, pv=pv, load=load, load_expected=expected,
                    battery=battery, tariff=tariff, start=start, seed=seed + 1)


def compare(sc: Scenario, policy: Policy | None, mpc: bool = True, log=print) -> dict:
    """Bills of every strategy on one scenario (calibrating the tariff's theta first)."""
    t0 = time.time()
    sc, thetas = S.calibrate(sc, (0.0, 0.5, 1.0))
    runs = [S.no_battery(sc), S.rule(sc)]
    if policy is not None:
        runs.append(S.agent(sc, policy))
    if mpc:
        runs.append(S.mpc(sc))
    runs.append(S.optimum(sc))
    bills = {r.name: r.settle(sc) for r in runs}
    base, best = bills["no_battery"]["total_zl"], bills["optimum"]["total_zl"]
    rows = []
    for r in runs:
        b = bills[r.name]
        saved = base - b["total_zl"]
        rows.append({"strategy": r.name, "bill_zl": round(b["total_zl"], 1), "savings_zl": round(saved, 1),
                     "share_of_optimum": round(saved / (base - best), 3) if base > best else None,
                     "import_kwh": round(float(r.grid_import.sum())), "export_kwh": round(float(r.grid_export.sum())),
                     "battery_out_kwh": round(float(r.discharge.sum())),
                     "lost_deposit_zl": round(b["lost_deposit_zl"], 1)})
    log(f"  done in {time.time() - t0:.0f}s (theta {sc.tariff.theta})")
    return {"theta": sc.tariff.theta, "theta_bills": {str(k): round(v, 1) for k, v in thetas.items()},
            "pv_kwh": round(float(sc.pv[sc.start:sc.end].sum())), "load_kwh": round(float(sc.load[sc.start:sc.end].sum())),
            "rows": rows}


def evaluate(cfg: Config = CONFIG, policy_path: Path = POLICY_PATH, mpc: bool = True, out: Path = REPORT_PATH,
             log=print) -> dict:
    policy = Policy(policy_path) if policy_path.is_file() else None
    lat, lon = LOCATIONS["Katowice"]
    inputs = test_inputs(lat, lon, cfg)
    report = {"test_period": [str(d) for d in TEST], "location": "Katowice", "policy": policy.meta if policy else None,
              "houses": []}
    for house in REFERENCE:
        log(house["name"])
        sc = scenario(inputs, house["planes"], house["annual_kwh"], house["battery"], house["tariff"])
        result = compare(sc, policy, mpc, log)
        for row in result["rows"]:
            log(f"    {row['strategy']:11s} bill {row['bill_zl']:8.1f} zł  saves {row['savings_zl']:7.1f} zł"
                + (f"  ({row['share_of_optimum']:.0%} of optimum)" if row["share_of_optimum"] is not None else ""))
        report["houses"].append({"name": house["name"], "annual_kwh": house["annual_kwh"], "planes": house["planes"],
                                 "battery": asdict(house["battery"]), "tariff": asdict(house["tariff"]), **result})
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"report saved to {out}")
    return report


def mean_share(report: dict, strategy: str) -> float:
    shares = [r["share_of_optimum"] for h in report["houses"] for r in h["rows"]
              if r["strategy"] == strategy and r["share_of_optimum"] is not None]
    return float(np.mean(shares)) if shares else float("nan")
