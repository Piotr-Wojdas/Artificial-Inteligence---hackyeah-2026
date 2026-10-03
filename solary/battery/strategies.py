"""Ways to run the battery over a scenario, and what each costs: the yardsticks for the agent.

  no_battery   the bill with PV but without a battery
  rule         what most hybrid inverters do: charge from surplus, discharge to cover the deficit
  agent        the trained policy (policy.py) choosing one of model.ACTIONS every quarter
  mpc          every hour, the cheapest plan for the next 36 h on the forecasts (linear program),
               executed with the same options as the agent
  optimum      the cheapest possible operation knowing the future exactly (linear program over the
               whole period): an upper bound no real controller reaches
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

from .data import STEP_H, STEPS_PER_DAY
from .model import ACTIONS, Battery, Scenario, apply, battery_flows, bill, observe


@dataclass
class Result:
    name: str
    cost: float                 # zł over steps start..end-1
    soc: np.ndarray             # stored kWh after each step
    charge: np.ndarray          # AC kWh
    discharge: np.ndarray
    grid_import: np.ndarray
    grid_export: np.ndarray
    actions: np.ndarray | None = None

    def settle(self, sc: Scenario) -> dict:
        """The exact bill over the scenario by the net-billing rules (see model.bill)."""
        return bill(sc.tariff, sc.rce_raw[sc.start:sc.end], self.grid_import, self.grid_export,
                    float(self.discharge.sum()) * sc.battery.wear_zl_per_kwh)

    def summary(self, baseline: "Result | None" = None) -> dict:
        out = {"strategy": self.name, "cost_zl": round(self.cost, 2),
               "import_kwh": round(float(self.grid_import.sum()), 1), "export_kwh": round(float(self.grid_export.sum()), 1),
               "cycles": round(float(self.discharge.sum()) / max(float(self.soc.max(initial=0)), 1e-9), 1)}
        if baseline is not None:
            out["savings_zl"] = round(baseline.cost - self.cost, 2)
        return out


def run(sc: Scenario, name: str, decide, soc0: float | None = None) -> Result:
    """Simulate steps start..end-1; decide(t, stored_kwh) -> (action index or None, charge, discharge)."""
    b = sc.battery
    soc = b.soc_min * b.capacity_kwh if soc0 is None else soc0
    n = sc.end - sc.start
    out = {k: np.zeros(n) for k in ("soc", "charge", "discharge", "import", "export")}
    actions = np.full(n, -1, int)
    cost = 0.0
    for i, t in enumerate(range(sc.start, sc.end)):
        action, charge, discharge = decide(t, soc)
        soc, c, flows = apply(sc, t, soc, charge, discharge)
        cost += c
        out["soc"][i] = soc
        for k in ("charge", "discharge", "import", "export"):
            out[k][i] = flows[k]
        if action is not None:
            actions[i] = action
    return Result(name, cost, out["soc"], out["charge"], out["discharge"], out["import"], out["export"],
                  actions if (actions >= 0).any() else None)


def no_battery(sc: Scenario) -> Result:
    return run(sc, "no_battery", lambda t, soc: (None, 0.0, 0.0))


def with_action(sc: Scenario, t: int, soc: float, action: int) -> tuple[int, float, float]:
    c, d = battery_flows(action, soc, sc.pv[t], sc.load[t], sc.battery)
    return action, c, d


def rule(sc: Scenario, soc0: float | None = None) -> Result:
    return run(sc, "rule", lambda t, soc: with_action(sc, t, soc, 0), soc0)


def agent(sc: Scenario, policy, soc0: float | None = None) -> Result:
    return run(sc, "agent", lambda t, soc: with_action(sc, t, soc, policy.act(observe(sc, t, soc))), soc0)


# ------------------------------------------------------------------ linear programs
def plan_lp(load: np.ndarray, pv: np.ndarray, buy: np.ndarray, sell: np.ndarray, b: Battery, soc0: float,
            terminal_value: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Cheapest (charge, discharge) per step for known load, PV and prices. Variables per step:
    charge, discharge, import, export, stored energy; stored energy left at the end is worth
    `terminal_value` zł/kWh."""
    n = len(load)
    eta, step = b.efficiency, b.power_kw * STEP_H
    # a quarter either imports or exports; where export pays more than import (RCE above a G11
    # price) a linear program would do both at once, so there export is valued at the import price
    sell = np.minimum(sell, buy - 1e-6)
    c, d, imp, exp, s = (np.arange(n) + k * n for k in range(5))
    rows = np.arange(n)
    # balance: import - export - charge + discharge = load - pv
    a1 = sparse.coo_matrix((np.concatenate([np.ones(n), -np.ones(n), -np.ones(n), np.ones(n)]),
                            (np.tile(rows, 4), np.concatenate([imp, exp, c, d]))), shape=(n, 5 * n))
    # storage: s_t - s_{t-1} - eta c_t + d_t / eta = 0  (s_{-1} = soc0)
    a2 = sparse.coo_matrix((np.concatenate([np.ones(n), -np.ones(n - 1), -eta * np.ones(n), np.ones(n) / eta]),
                            (np.concatenate([rows, rows[1:], rows, rows]),
                             np.concatenate([s, s[:-1], c, d]))), shape=(n, 5 * n))
    a_eq = sparse.vstack([a1, a2]).tocsc()
    b_eq = np.concatenate([load - pv, np.r_[soc0, np.zeros(n - 1)]])
    cost = np.concatenate([np.zeros(n), np.full(n, b.wear_zl_per_kwh), buy, -sell, np.zeros(n)])
    cost[s[-1]] -= terminal_value
    bounds = ([(0, step)] * (2 * n) + [(0, None)] * (2 * n)
              + [(b.soc_min * b.capacity_kwh, b.soc_max * b.capacity_kwh)] * n)
    res = linprog(cost, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"battery LP failed: {res.message}")
    x = res.x
    return x[c], x[d]


def optimum(sc: Scenario, soc0: float | None = None) -> Result:
    """Perfect foresight over the whole scenario (solved month by month, chained)."""
    b = sc.battery
    soc = b.soc_min * b.capacity_kwh if soc0 is None else soc0
    charge, discharge = np.zeros(sc.end - sc.start), np.zeros(sc.end - sc.start)
    block = 31 * STEPS_PER_DAY
    for t0 in range(sc.start, sc.end, block):
        t1 = min(t0 + block, sc.end)
        t2 = min(t1 + 2 * STEPS_PER_DAY, len(sc.load))        # look 2 days past the block, then cut
        c, d = plan_lp(sc.load[t0:t2], sc.pv[t0:t2], sc.buy[t0:t2], sc.sell[t0:t2], b, soc)
        charge[t0 - sc.start:t1 - sc.start], discharge[t0 - sc.start:t1 - sc.start] = c[:t1 - t0], d[:t1 - t0]
        soc = soc + float((c[:t1 - t0] * b.efficiency - d[:t1 - t0] / b.efficiency).sum())
    return run(sc, "optimum", lambda t, s: (None, charge[t - sc.start], discharge[t - sc.start]), soc0)


def to_action(charge: float, discharge: float, surplus: float, b: Battery) -> int:
    """The option that carries out a planned (charge, discharge) given the expected surplus."""
    step = b.power_kw * STEP_H
    if charge < 1e-3 and discharge < 1e-3:
        return ACTIONS.index("idle")
    if charge >= discharge:
        if charge <= max(surplus, 0) + 0.25 * step:
            return ACTIONS.index("charge_surplus")
        return ACTIONS.index("charge_full" if charge > 0.75 * step else "charge_half")
    if discharge <= max(-surplus, 0) + 0.25 * step:
        return ACTIONS.index("cover_deficit")
    return ACTIONS.index("discharge_full" if discharge > 0.75 * step else "discharge_half")


def mpc(sc: Scenario, soc0: float | None = None, horizon_h: int = 36, every: int = 4) -> Result:
    """Re-plan every `every` quarters for the next `horizon_h` hours on the forecasts."""
    b = sc.battery
    plan: dict[int, int] = {}

    def decide(t, soc):
        if t not in plan:
            n = min(horizon_h * 4, len(sc.load) - t)
            rce = sc.price_forecast(t, n)
            pv, load = sc.pv_forecast(t, n), sc.load_expected[t:t + n]
            buy, sell = sc.tariff.buy(rce), sc.tariff.sell(rce)
            value = 0.5 * (float(buy.mean()) + float(sell.mean())) * b.efficiency   # energy left at the end
            c, d = plan_lp(load, pv, buy, sell, b, soc, terminal_value=value)
            for k in range(min(every, n)):
                plan[t + k] = to_action(c[k], d[k], pv[k] - load[k], b)
        return with_action(sc, t, soc, plan[t])

    return run(sc, "mpc", decide, soc0)


def with_tariff(sc: Scenario, **changes) -> Scenario:
    """The same scenario (same forecast errors) with tariff fields changed."""
    return Scenario(index=sc.index, rce=sc.rce_raw, pv=sc.pv, load=sc.load, load_expected=sc.load_expected,
                    battery=sc.battery, tariff=dataclasses.replace(sc.tariff, **changes), start=sc.start,
                    end=sc.end, seed=sc.seed, pv_error_scale=sc.pv_error_scale, known_rule=sc.known_rule)


def calibrate(sc: Scenario, thetas=(0.0, 0.25, 0.5, 0.75, 1.0)) -> tuple[Scenario, dict[float, float]]:
    """The scenario with the tariff's `theta` that gives the lowest exact bill when the battery is
    run optimally: how much an exported kWh is really worth to this house over the year."""
    bills = {th: optimum(with_tariff(sc, theta=th)).settle(sc)["total_zl"] for th in thetas}
    best = min(bills, key=bills.get)
    return with_tariff(sc, theta=best), bills
