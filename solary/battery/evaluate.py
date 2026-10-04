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
from .model import Battery, Grid, Scenario, Tariff
from .policy import POLICY_PATH, Policy
from .train import LOCATIONS, TEST

REPORT_PATH = Path(__file__).with_name("evaluation.json")
STRATEGIES = ("no_battery", "rule", "agent", "lp_mpc", "dp_mpc", "optimum")
WEAK = Grid(export_limit_kw=3.0)       # a weak rural line: the inverter trips above 3 kW of export at midday
REFERENCE = [
    {"name": "6 kWp na południe, bateria 10 kWh, G11, mocna sieć", "planes": [(6.0, 35, 180)], "annual_kwh": 4000,
     "battery": Battery(10, 5), "tariff": Tariff("g11"), "grid": Grid()},
    {"name": "6 kWp na południe, bateria 10 kWh, G11, słaba sieć (wyłącza powyżej 3 kW)", "planes": [(6.0, 35, 180)],
     "annual_kwh": 4000, "battery": Battery(10, 5), "tariff": Tariff("g11"), "grid": WEAK},
    {"name": "6 kWp na południe, bateria 10 kWh, taryfa dynamiczna, słaba sieć", "planes": [(6.0, 35, 180)],
     "annual_kwh": 4000, "battery": Battery(10, 5), "tariff": Tariff("dynamic"), "grid": WEAK},
    {"name": "8 kWp wschód-zachód, bateria 5 kWh, G11, słaba sieć (4 kW)", "planes": [(4.0, 30, 90), (4.0, 30, 270)],
     "annual_kwh": 5000, "battery": Battery(5, 3), "tariff": Tariff("g11"), "grid": Grid(export_limit_kw=4.0)},
    {"name": "4 kWp na południe, bateria 15 kWh, taryfa dynamiczna, mocna sieć", "planes": [(4.0, 35, 180)],
     "annual_kwh": 6000, "battery": Battery(15, 7.5), "tariff": Tariff("dynamic"), "grid": Grid()},
]


def test_inputs(lat: float, lon: float, cfg: Config = CONFIG) -> Inputs:
    """Prices and weather for the test year plus a day before (forecasts) and the horizon after."""
    return history(lat, lon, TEST[0] - timedelta(days=1), TEST[1], cfg)


def scenario(inputs: Inputs, planes, annual_kwh: float, battery: Battery, tariff: Tariff, grid: Grid = Grid(),
             pv_kwh_year: float | None = None, seed: int = 0) -> Scenario:
    """The house over the test year. `pv_kwh_year` rescales the PV to a known yearly production
    (e.g. the roof analysis, which includes local shade)."""
    pv = inputs.pv(list(planes))
    start = int(inputs.index.get_loc(local_day_bounds(TEST[0], TEST[0])[0]))
    if pv_kwh_year:
        pv = pv * pv_kwh_year / max(float(pv[start:].sum()), 1e-9)
    load, expected = load_profile(inputs.index, annual_kwh, seed=seed)
    return Scenario(index=inputs.index, rce=inputs.rce, pv=pv, load=load, load_expected=expected,
                    battery=battery, tariff=tariff, grid=grid, start=start, seed=seed + 1)


def rows(sc: Scenario, runs: list) -> list[dict]:
    """Exact yearly bills and how much of the possible savings each strategy gets."""
    bills = {r.name: r.settle(sc) for r in runs}
    base = bills["no_battery"]["total_zl"]
    best = bills["optimum"]["total_zl"] if "optimum" in bills else None
    out = []
    for r in runs:
        b = bills[r.name]
        saved = base - b["total_zl"]
        out.append({"strategy": r.name, "bill_zl": round(b["total_zl"], 1), "savings_zl": round(saved, 1),
                    "share_of_optimum": round(saved / (base - best), 3) if best is not None and base > best else None,
                    "trips": b["trips"], "lost_pv_kwh": round(b["lost_pv_kwh"]),
                    "import_kwh": round(float(r.grid_import.sum())), "export_kwh": round(float(r.grid_export.sum())),
                    "battery_out_kwh": round(float(r.discharge.sum())), "wear_zl": round(b["wear_zl"], 1),
                    "lost_deposit_zl": round(b["lost_deposit_zl"], 1)})
    return out


def compare(sc: Scenario, policy: Policy | None, strategies=STRATEGIES, log=print) -> dict:
    """Bills of every strategy on one scenario (calibrating the tariff's theta first)."""
    t0 = time.time()
    sc, thetas = S.calibrate(sc)
    runs = []
    for name in strategies:
        if name == "agent":
            if policy is not None:
                runs.append(S.agent(sc, policy))
        else:
            runs.append(getattr(S, name)(sc))
    log(f"  done in {time.time() - t0:.0f}s (theta {sc.tariff.theta})")
    return {"theta": sc.tariff.theta, "theta_bills": {str(k): round(v, 1) for k, v in thetas.items()},
            "pv_kwh": round(float(sc.pv[sc.start:sc.end].sum())), "load_kwh": round(float(sc.load[sc.start:sc.end].sum())),
            "rows": rows(sc, runs)}


def _one(args) -> dict:
    """One reference house (run in a separate process by evaluate)."""
    i, cfg, policy_path, strategies = args
    house = REFERENCE[i]
    policy = Policy(policy_path) if policy_path.is_file() else None
    sc = scenario(test_inputs(*LOCATIONS["Katowice"], cfg), house["planes"], house["annual_kwh"], house["battery"],
                  house["tariff"], house["grid"])
    result = compare(sc, policy, strategies, log=lambda m: None)
    return {"name": house["name"], "annual_kwh": house["annual_kwh"], "planes": house["planes"],
            "battery": asdict(house["battery"]), "tariff": asdict(house["tariff"]), "grid": asdict(house["grid"]),
            **result}


def evaluate(cfg: Config = CONFIG, policy_path: Path = POLICY_PATH, strategies=STRATEGIES, out: Path = REPORT_PATH,
             workers: int = 5, log=print) -> dict:
    """Every strategy on every reference house, the houses in parallel (each MPC takes minutes)."""
    from concurrent.futures import ProcessPoolExecutor

    policy = Policy(policy_path) if policy_path.is_file() else None
    test_inputs(*LOCATIONS["Katowice"], cfg)                                  # download once, before the workers
    t0 = time.time()
    with ProcessPoolExecutor(max(1, workers)) as pool:
        houses = list(pool.map(_one, [(i, cfg, policy_path, strategies) for i in range(len(REFERENCE))]))
    for h in houses:
        log(h["name"])
        for row in h["rows"]:
            log(f"    {row['strategy']:11s} bill {row['bill_zl']:8.1f} zł  saves {row['savings_zl']:7.1f} zł  trips {row['trips']:4d}"
                + (f"  ({row['share_of_optimum']:.0%} of optimum)" if row["share_of_optimum"] is not None else ""))
    report = {"test_period": [str(d) for d in TEST], "location": "Katowice", "policy": policy.meta if policy else None,
              "houses": houses}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"report saved to {out} ({time.time() - t0:.0f}s)")
    return report


def mean_share(report: dict, strategy: str) -> float:
    shares = [r["share_of_optimum"] for h in report["houses"] for r in h["rows"]
              if r["strategy"] == strategy and r["share_of_optimum"] is not None]
    return float(np.mean(shares)) if shares else float("nan")
