"""The battery plan for one house: what the agent does from now to the end of tomorrow, on the
prices PSE has published and the weather forecast, and what the battery saves over a year.

`battery_report()` is what the CLI and the HTTP API return.
"""

from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd

from ..config import CONFIG, Config
from . import strategies as S
from .data import STEPS_PER_DAY, TZ, live, load_profile
from .evaluate import rows, scenario, test_inputs
from .model import ACTIONS, HORIZON, Battery, Grid, Scenario, Tariff, observe, step_action
from .policy import Policy, available
from .train import TEST

ACTION_LABELS = {
    "self_consumption": "autokonsumpcja", "idle": "bez ruchu", "charge_surplus": "ładuj z nadwyżki PV",
    "absorb_peak": "ładuj tylko szczyt ponad limit sieci", "cover_deficit": "pokrywaj zużycie, nadwyżkę sprzedaj",
    "charge_25": "ładuj z sieci (25% mocy)", "charge_50": "ładuj z sieci (50% mocy)",
    "charge_100": "ładuj z sieci (pełna moc)", "discharge_25": "sprzedawaj z baterii (25% mocy)",
    "discharge_50": "sprzedawaj z baterii (50% mocy)", "discharge_100": "sprzedawaj z baterii (pełna moc)",
}
_CACHE: dict = {}


def effect_label(flows: dict, pv: float, load: float) -> str:
    """What actually happens in a quarter (the chosen option may have no effect, e.g. selling
    from an empty battery)."""
    if flows.get("trip"):
        return "falownik wyłączony przez napięcie sieci: produkcja stracona"
    c, d, imp, exp = flows["charge"], flows["discharge"], flows["import"], flows["export"]
    if c > 1e-3:
        return "ładuje z sieci" if imp > 1e-3 and c > max(pv - load, 0.0) + 1e-3 else "ładuje z nadwyżki PV"
    if d > 1e-3:
        return "sprzedaje prąd z baterii" if exp > 1e-3 else "zasila dom z baterii"
    if exp > 1e-3:
        return "sprzedaje nadwyżkę PV"
    return "dom bierze prąd z sieci" if imp > 1e-3 else "bez ruchu"


def yearly(lat: float, lon: float, planes, annual_kwh: float, battery: Battery, tariff: Tariff, grid: Grid,
           pv_kwh_year: float | None, policy: Policy | None, cfg: Config = CONFIG) -> tuple[dict, Tariff]:
    """Bills over the test year without a battery, with the usual inverter, with the agent and at
    the optimum; and the tariff calibrated for this house (theta). Cached per house."""
    key = (round(lat, 4), round(lon, 4), tuple(map(tuple, planes)), annual_kwh, battery, tariff, grid, pv_kwh_year,
           policy.meta.get("trained_at") if policy else None)
    if key in _CACHE:
        return _CACHE[key]
    sc = scenario(test_inputs(lat, lon, cfg), planes, annual_kwh, battery, tariff, grid, pv_kwh_year)
    sc, _ = S.calibrate(sc, (0.0, 1.0))
    runs = [S.no_battery(sc), S.rule(sc)] + ([S.agent(sc, policy)] if policy else []) + [S.optimum(sc)]
    out = ({"period": [str(d) for d in TEST], "pv_kwh": round(float(sc.pv[sc.start:sc.end].sum())),
            "load_kwh": round(float(sc.load[sc.start:sc.end].sum())), "theta": sc.tariff.theta,
            "rows": rows(sc, runs)}, sc.tariff)
    _CACHE[key] = out
    return out


def daily_plan(lat: float, lon: float, planes, annual_kwh: float, battery: Battery, tariff: Tariff, grid: Grid,
               soc_now: float, policy: Policy | None, pv_scale: float = 1.0, cfg: Config = CONFIG) -> dict:
    """From the current quarter to the end of tomorrow: the agent's (or, without a trained agent,
    the rule's) action every quarter, rolled out on the forecasts."""
    inputs = live(lat, lon, cfg, days=4)
    now = pd.Timestamp.now(tz="UTC").floor("15min")
    start = int(np.searchsorted(inputs.index.asi8, now.as_unit("ns").value))
    tomorrow_end = (pd.Timestamp.now(tz=TZ).normalize() + pd.Timedelta(days=2)).tz_convert("UTC")
    end = int(np.searchsorted(inputs.index.asi8, tomorrow_end.as_unit("ns").value))
    end = min(end, len(inputs.index) - HORIZON)
    pv = inputs.pv(list(planes)) * pv_scale
    _, expected = load_profile(inputs.index, annual_kwh)
    sc = Scenario(index=inputs.index, rce=inputs.rce, pv=pv, load=expected, load_expected=expected, battery=battery,
                  tariff=tariff, grid=grid, start=start, end=end, pv_error_scale=0.0, known_rule="nan")
    soc = rule_soc = soc_now * battery.capacity_kwh
    steps, cost, rule_cost, rule_trips = [], 0.0, 0.0, 0
    money = {"buy_zl": 0.0, "sell_zl": 0.0, "wear_zl": 0.0}
    for t in range(sc.start, sc.end):
        action = policy.act(observe(sc, t, soc)) if policy else 0
        soc, step_cost, flows = step_action(sc, t, soc, action)
        rule_soc, rule_step, rule_flows = step_action(sc, t, rule_soc, 0)
        cost, rule_cost, rule_trips = cost + step_cost, rule_cost + rule_step, rule_trips + rule_flows["trip"]
        money["buy_zl"] += flows["import"] * sc.buy[t]
        money["sell_zl"] += flows["export"] * sc.sell[t]
        money["wear_zl"] += flows["wear"]
        steps.append({"time": sc.index[t].tz_convert(TZ).isoformat(), "rce_zl_kwh": round(float(sc.rce_raw[t]), 4)
                      if np.isfinite(sc.rce_raw[t]) else None,
                      "buy_zl_kwh": round(float(sc.buy[t]), 4), "sell_zl_kwh": round(float(sc.sell[t]), 4),
                      "pv_kwh": round(float(sc.pv[t]), 3), "load_kwh": round(float(sc.load[t]), 3),
                      "action": ACTIONS[action], "action_label": ACTION_LABELS[ACTIONS[action]],
                      "effect_label": effect_label(flows, float(sc.pv[t]), float(sc.load[t])),
                      "charge_kwh": round(flows["charge"], 3), "discharge_kwh": round(flows["discharge"], 3),
                      "import_kwh": round(flows["import"], 3), "export_kwh": round(flows["export"], 3),
                      "trip": flows["trip"], "export_limited": bool(np.isfinite(sc.cap[t])),
                      "soc": round(soc / battery.capacity_kwh, 3)})
    published = inputs.index[np.isfinite(inputs.rce)]
    return {"controller": "agent" if policy else "rule", "now": steps[0] if steps else None, "steps": steps,
            "cost_zl": round(cost, 2), "rule_cost_zl": round(rule_cost, 2),
            "trips": sum(s["trip"] for s in steps), "rule_trips": int(rule_trips),
            **{k: round(v, 2) for k, v in money.items()},
            "prices_until": published[-1].tz_convert(TZ).isoformat() if len(published) else None}


def battery_report(lat: float, lon: float, planes, annual_kwh: float = 4000.0, battery: Battery = Battery(),
                   tariff: Tariff = Tariff(), grid: Grid = Grid(), soc_now: float = 0.5,
                   pv_kwh_year: float | None = None, plan: bool = True, cfg: Config = CONFIG) -> dict:
    """Yearly savings and (with `plan`) today's and tomorrow's plan for one house."""
    policy = Policy() if available() else None
    year, tariff = yearly(lat, lon, planes, annual_kwh, battery, tariff, grid, pv_kwh_year, policy, cfg)
    out = {"house": {"lat": lat, "lon": lon, "planes": [list(p) for p in planes], "annual_kwh": annual_kwh,
                     "battery": asdict(battery), "tariff": asdict(tariff), "grid": asdict(grid)},
           "agent": policy.meta if policy else None, "year": year, "plan": None,
           "attribution": "Ceny: PSE (RCE). Pogoda: NASA POWER (historia), Open-Meteo (prognoza)."}
    if plan:
        model_pv = scenario(test_inputs(lat, lon, cfg), planes, annual_kwh, battery, tariff, grid).pv
        scale = (pv_kwh_year / max(float(model_pv[-365 * STEPS_PER_DAY:].sum()), 1e-9)) if pv_kwh_year else 1.0
        out["plan"] = daily_plan(lat, lon, planes, annual_kwh, battery, tariff, grid, soc_now, policy, scale, cfg)
    return out
