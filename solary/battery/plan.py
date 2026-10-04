"""The battery plan for one house: what the agent does from now to the end of tomorrow, on the
prices PSE has published and the weather forecast, and what the battery saves over a year.

`battery_report()` is what the CLI and the HTTP API return.
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict
from datetime import timedelta

import numpy as np
import pandas as pd

from ..config import CONFIG, Config
from . import strategies as S
from .data import STEPS_PER_DAY, TZ, live, load_profile
from .evaluate import scenario, test_inputs
from .model import ACTIONS, HORIZON, Battery, Scenario, Tariff, apply, battery_flows, observe
from .policy import Policy, available
from .train import TEST

ACTION_LABELS = {
    "self_consumption": "autokonsumpcja", "idle": "bez ruchu", "charge_surplus": "ładuj z nadwyżki PV",
    "cover_deficit": "pokrywaj zużycie, nadwyżkę sprzedaj", "charge_half": "ładuj z sieci (pół mocy)",
    "charge_full": "ładuj z sieci (pełna moc)", "discharge_half": "sprzedawaj z baterii (pół mocy)",
    "discharge_full": "sprzedawaj z baterii (pełna moc)",
}


def effect_label(flows: dict, pv: float, load: float) -> str:
    """What actually happens in a quarter (the chosen option may have no effect, e.g. selling
    from an empty battery)."""
    c, d, imp, exp = flows["charge"], flows["discharge"], flows["import"], flows["export"]
    if c > 1e-3:
        return "ładuje z sieci" if imp > 1e-3 and c > max(pv - load, 0.0) + 1e-3 else "ładuje z nadwyżki PV"
    if d > 1e-3:
        return "sprzedaje prąd z baterii" if exp > 1e-3 else "zasila dom z baterii"
    if exp > 1e-3:
        return "sprzedaje nadwyżkę PV"
    return "dom bierze prąd z sieci" if imp > 1e-3 else "bez ruchu"


def yearly(lat: float, lon: float, planes, annual_kwh: float, battery: Battery, tariff: Tariff,
           pv_kwh_year: float | None, policy: Policy | None, cfg: Config = CONFIG) -> tuple[dict, Tariff]:
    """Bills over the test year without a battery, with the usual rule, with the agent and at the
    optimum; and the tariff calibrated for this house (theta)."""
    sc = scenario(test_inputs(lat, lon, cfg), planes, annual_kwh, battery, tariff, pv_kwh_year)
    sc, _ = S.calibrate(sc, (0.0, 0.5, 1.0))
    runs = [S.no_battery(sc), S.rule(sc)] + ([S.agent(sc, policy)] if policy else []) + [S.optimum(sc)]
    bills = {r.name: r.settle(sc) for r in runs}
    base, best = bills["no_battery"]["total_zl"], bills["optimum"]["total_zl"]
    rows = [{"strategy": r.name, "bill_zl": round(bills[r.name]["total_zl"], 1),
             "savings_zl": round(base - bills[r.name]["total_zl"], 1),
             "share_of_optimum": round((base - bills[r.name]["total_zl"]) / (base - best), 3) if base > best else None}
            for r in runs]
    return {"period": [str(d) for d in TEST], "pv_kwh": round(float(sc.pv[sc.start:sc.end].sum())),
            "load_kwh": round(float(sc.load[sc.start:sc.end].sum())), "theta": sc.tariff.theta, "rows": rows}, sc.tariff


def daily_plan(lat: float, lon: float, planes, annual_kwh: float, battery: Battery, tariff: Tariff,
               soc_now: float, policy: Policy | None, pv_scale: float = 1.0, cfg: Config = CONFIG) -> dict:
    """From the current quarter to the end of tomorrow: the agent's (or, without a trained agent,
    the rule's) action every quarter, rolled out on the forecasts."""
    inputs = live(lat, lon, cfg, days=3)
    now = pd.Timestamp.now(tz="UTC").floor("15min")
    start = int(np.searchsorted(inputs.index.asi8, now.as_unit("ns").value))
    tomorrow_end = (pd.Timestamp.now(tz=TZ).normalize() + pd.Timedelta(days=2)).tz_convert("UTC")
    end = int(np.searchsorted(inputs.index.asi8, tomorrow_end.as_unit("ns").value))
    end = min(end, len(inputs.index) - HORIZON)
    pv = inputs.pv(list(planes)) * pv_scale
    _, expected = load_profile(inputs.index, annual_kwh)
    sc = Scenario(index=inputs.index, rce=inputs.rce, pv=pv, load=expected, load_expected=expected,
                  battery=battery, tariff=tariff, start=start, end=end, pv_error_scale=0.0, known_rule="nan")
    soc = soc_now * battery.capacity_kwh
    steps, cost, rule_cost = [], 0.0, 0.0
    money = {"buy_zl": 0.0, "sell_zl": 0.0, "wear_zl": 0.0}
    rule_soc = soc
    for t in range(sc.start, sc.end):
        action = policy.act(observe(sc, t, soc)) if policy else 0
        c, d = battery_flows(action, soc, sc.pv[t], sc.load[t], battery)
        soc, step_cost, flows = apply(sc, t, soc, c, d)
        rc, rd = battery_flows(0, rule_soc, sc.pv[t], sc.load[t], battery)
        rule_soc, rule_step, _ = apply(sc, t, rule_soc, rc, rd)
        cost, rule_cost = cost + step_cost, rule_cost + rule_step
        money["buy_zl"] += flows["import"] * sc.buy[t]
        money["sell_zl"] += flows["export"] * sc.sell[t]
        money["wear_zl"] += flows["discharge"] * battery.wear_zl_per_kwh
        steps.append({"time": sc.index[t].tz_convert(TZ).isoformat(), "rce_zl_kwh": round(float(sc.rce_raw[t]), 4)
                      if np.isfinite(sc.rce_raw[t]) else None,
                      "buy_zl_kwh": round(float(sc.buy[t]), 4), "sell_zl_kwh": round(float(sc.sell[t]), 4),
                      "pv_kwh": round(float(sc.pv[t]), 3), "load_kwh": round(float(sc.load[t]), 3),
                      "action": ACTIONS[action], "action_label": ACTION_LABELS[ACTIONS[action]],
                      "effect_label": effect_label(flows, float(sc.pv[t]), float(sc.load[t])),
                      "charge_kwh": round(flows["charge"], 3), "discharge_kwh": round(flows["discharge"], 3),
                      "import_kwh": round(flows["import"], 3), "export_kwh": round(flows["export"], 3),
                      "soc": round(soc / battery.capacity_kwh, 3)})
    published = inputs.index[np.isfinite(inputs.rce)]
    return {"controller": "agent" if policy else "rule", "now": steps[0] if steps else None, "steps": steps,
            "cost_zl": round(cost, 2), "rule_cost_zl": round(rule_cost, 2),
            **{k: round(v, 2) for k, v in money.items()},
            "prices_until": published[-1].tz_convert(TZ).isoformat() if len(published) else None}


def battery_report(lat: float, lon: float, planes, annual_kwh: float = 4000.0, battery: Battery = Battery(),
                   tariff: Tariff = Tariff(), soc_now: float = 0.5, pv_kwh_year: float | None = None,
                   plan: bool = True, cfg: Config = CONFIG) -> dict:
    """Yearly savings and (with `plan`) today's and tomorrow's plan for one house."""
    policy = Policy() if available() else None
    year, tariff = yearly(lat, lon, planes, annual_kwh, battery, tariff, pv_kwh_year, policy, cfg)
    out = {"house": {"lat": lat, "lon": lon, "planes": [list(p) for p in planes], "annual_kwh": annual_kwh,
                     "battery": asdict(battery), "tariff": asdict(tariff)},
           "agent": policy.meta if policy else None, "year": year, "plan": None,
           "attribution": "Ceny: PSE (RCE). Pogoda: NASA POWER (historia), Open-Meteo (prognoza)."}
    if plan:
        model_pv = scenario(test_inputs(lat, lon, cfg), planes, annual_kwh, battery, tariff).pv
        scale = (pv_kwh_year / max(float(model_pv[-365 * STEPS_PER_DAY:].sum()), 1e-9)) if pv_kwh_year else 1.0
        out["plan"] = daily_plan(lat, lon, planes, annual_kwh, battery, tariff, soc_now, policy, scale, cfg)
    return out
