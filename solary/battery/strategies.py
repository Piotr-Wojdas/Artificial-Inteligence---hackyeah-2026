"""Ways to run the battery over a scenario, and what each costs: the yardsticks for the agent.

  no_battery   the bill with PV but without a battery
  rule         what most hybrid inverters do: charge from surplus, discharge to cover the deficit
  agent        the trained policy (policy.py) choosing one of model.ACTIONS every quarter
  lp_mpc       every hour the cheapest plan for the next 36 h on the forecasts, as a linear program:
               the usual industrial approach, so the physics is linearised (constant efficiency,
               linear wear, an export cap instead of trips)
  dp_mpc       the same every hour, but by dynamic programming over the state of charge with the exact
               (nonlinear) physics and the agent's options: a strong, slower planner that still
               trusts the forecast
  optimum      dynamic programming with the exact physics and the whole future known: the best any
               controller with these options could do (an upper bound, not reachable in practice)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.optimize import linprog

from .data import STEP_H, STEPS_PER_DAY
from .model import (ACTIONS, HORIZON, Battery, Scenario, apply, battery_flows, bill, observe, step_action,
                    transition)

GRID_POINTS = 121                     # state-of-charge grid of the dynamic programs
_A = np.arange(len(ACTIONS))[:, None]


@dataclass
class Result:
    name: str
    cost: float                 # zł over steps start..end-1 (marginal prices, wear included)
    soc: np.ndarray             # stored kWh after each step
    charge: np.ndarray          # AC kWh
    discharge: np.ndarray
    grid_import: np.ndarray
    grid_export: np.ndarray
    wear: np.ndarray            # zł of wear and ageing per step
    trips: np.ndarray           # bool per step
    lost_pv: np.ndarray         # kWh of PV lost to trips
    actions: np.ndarray | None = None

    def settle(self, sc: Scenario) -> dict:
        """The exact bill over the scenario by the net-billing rules (see model.bill)."""
        out = bill(sc.tariff, sc.rce_raw[sc.start:sc.end], self.grid_import, self.grid_export, float(self.wear.sum()))
        return {**out, "trips": int(self.trips.sum()), "lost_pv_kwh": float(self.lost_pv.sum())}


def run(sc: Scenario, name: str, decide, soc0: float | None = None) -> Result:
    """Simulate steps start..end-1; decide(t, stored_kwh) -> action index (reactive, see
    model.battery_flows) or (None, charge, discharge) for fixed AC energies."""
    b = sc.battery
    soc = b.soc_min * b.capacity_kwh if soc0 is None else soc0
    n = sc.end - sc.start
    keys = ("charge", "discharge", "import", "export", "wear", "lost_pv")
    out = {k: np.zeros(n) for k in ("soc", *keys)}
    trips, actions = np.zeros(n, bool), np.full(n, -1, int)
    cost = 0.0
    for i, t in enumerate(range(sc.start, sc.end)):
        d = decide(t, soc)
        if isinstance(d, tuple):
            soc, c, f = apply(sc, t, soc, d[1], d[2])
        else:
            actions[i] = d
            soc, c, f = step_action(sc, t, soc, d)
        cost += c
        out["soc"][i] = soc
        for k in keys:
            out[k][i] = f[k]
        trips[i] = f["trip"]
    return Result(name, cost, out["soc"], out["charge"], out["discharge"], out["import"], out["export"],
                  out["wear"], trips, out["lost_pv"], actions if (actions >= 0).any() else None)


def no_battery(sc: Scenario) -> Result:
    return run(sc, "no_battery", lambda t, soc: (None, 0.0, 0.0))


def rule(sc: Scenario, soc0: float | None = None) -> Result:
    return run(sc, "rule", lambda t, soc: 0, soc0)


def agent(sc: Scenario, policy, soc0: float | None = None) -> Result:
    return run(sc, "agent", lambda t, soc: policy.act(observe(sc, t, soc)), soc0)


# ------------------------------------------------------------------ dynamic programming (exact physics)
def soc_grid(b: Battery, points: int = GRID_POINTS) -> np.ndarray:
    return np.linspace(b.soc_min * b.capacity_kwh, b.soc_max * b.capacity_kwh, points)


def values(b: Battery, pv, load, buy, sell, cap, terminal: np.ndarray | None = None,
           grid: np.ndarray | None = None, chunk: int = STEPS_PER_DAY) -> np.ndarray:
    """Backward induction: V[k, i] = cheapest cost from step k on, starting with grid[i] kWh stored,
    choosing among ACTIONS with the exact physics. V[n] = terminal (cost of what is left).
    The transitions do not depend on V, so they are computed a day at a time for every step,
    action and grid point at once; the backward pass only interpolates."""
    s = soc_grid(b) if grid is None else grid
    pv, load, buy, sell, cap = (np.asarray(x, float) for x in (pv, load, buy, sell, cap))
    n = len(pv)
    v = np.zeros((n + 1, len(s)))
    if terminal is not None:
        v[n] = terminal
    soc, act = s[None, None, :], _A[None, :, :]
    for k0 in range(((n - 1) // chunk) * chunk, -1, -chunk):
        k1 = min(k0 + chunk, n)
        col = lambda x: x[k0:k1, None, None]                                  # noqa: E731
        c, d = battery_flows(act, soc, col(pv), col(load), b, col(cap))
        new, cost, _ = transition(b, soc, c, d, col(pv), col(load), col(buy), col(sell), col(cap))
        for k in range(k1 - 1, k0 - 1, -1):
            v[k] = (cost[k - k0] + np.interp(new[k - k0], s, v[k + 1])).min(axis=0)
    return v


def best_action(b: Battery, soc: float, pv, load, buy, sell, cap, v_next: np.ndarray, grid: np.ndarray) -> int:
    """The action with the lowest cost now + value of where it leaves the battery."""
    c, d = battery_flows(_A[:, 0], soc, pv, load, b, cap)
    new, cost, _ = transition(b, soc, c, d, pv, load, buy, sell, cap)
    return int(np.argmin(cost + np.interp(new, grid, v_next)))


def optimum(sc: Scenario, soc0: float | None = None) -> Result:
    """Perfect foresight with the exact physics (one backward pass, then the policy forward)."""
    b, s = sc.battery, soc_grid(sc.battery)
    t0, t1 = sc.start, sc.end
    v = values(b, sc.pv[t0:t1], sc.load[t0:t1], sc.buy[t0:t1], sc.sell[t0:t1], sc.cap[t0:t1], grid=s)
    return run(sc, "optimum", lambda t, soc: best_action(b, soc, sc.pv[t], sc.load[t], sc.buy[t], sc.sell[t],
                                                         sc.cap[t], v[t - t0 + 1], s), soc0)


def _forecast(sc: Scenario, t: int, n: int):
    rce = sc.price_forecast(t, n)
    return (sc.pv_forecast(t, n), sc.load_expected[t:t + n], sc.tariff.buy(rce), sc.tariff.sell(rce),
            sc.trip_cap(rce))


def _left_value(b: Battery, buy: np.ndarray, sell: np.ndarray) -> float:
    """zł per stored kWh left at the end of a plan: between selling it and saving a purchase."""
    return 0.5 * (float(buy.mean()) + float(sell.mean())) * b.one_way_efficiency(0.5)


def dp_mpc(sc: Scenario, soc0: float | None = None, horizon_h: int = 36, every: int = 4) -> Result:
    """Every `every` quarters: dynamic programming over the next `horizon_h` hours on the forecasts
    (exact physics), then that plan's best action each quarter."""
    b, s = sc.battery, soc_grid(sc.battery)
    plan: dict = {}

    def decide(t, soc):
        if not plan or t >= plan["t"] + every:
            n = min(horizon_h * 4, len(sc.load) - t)
            pv, load, buy, sell, cap = _forecast(sc, t, n)
            terminal = -_left_value(b, buy, sell) * (s - s[0])
            plan.update(t=t, f=(pv, load, buy, sell, cap), v=values(b, pv, load, buy, sell, cap, terminal, s))
        k = t - plan["t"]
        pv, load, buy, sell, cap = plan["f"]
        return best_action(b, soc, pv[k], load[k], buy[k], sell[k], cap[k], plan["v"][k + 1], s)

    return run(sc, "dp_mpc", decide, soc0)


# ------------------------------------------------------------------ linear program (linearised physics)
def plan_lp(load: np.ndarray, pv: np.ndarray, buy: np.ndarray, sell: np.ndarray, b: Battery, soc0: float,
            terminal_value: float = 0.0, cap: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cheapest (charge, discharge, export) per step with the physics linearised: constant efficiency
    (at half power), linear wear, and an export cap where a trip threatens (PV may be curtailed to
    respect it). Variables per step: charge, discharge, import, export, curtailment, stored energy."""
    n = len(load)
    eta, step = b.one_way_efficiency(0.5), b.power_kw * STEP_H
    wear = 0.5 * b.wear_zl_per_kwh * (1 - b.crate_wear + 0.5 * b.crate_wear)
    # a quarter either imports or exports; where export pays more than import (RCE above a G11
    # price) a linear program would do both at once, so there export is valued at the import price
    sell = np.minimum(sell, buy - 1e-6)
    c, d, imp, exp, cut, s = (np.arange(n) + k * n for k in range(6))
    rows = np.arange(n)
    # balance: import - export - charge + discharge - curtailment = load - pv
    a1 = sparse.coo_matrix((np.concatenate([np.ones(n), -np.ones(n), -np.ones(n), np.ones(n), -np.ones(n)]),
                            (np.tile(rows, 5), np.concatenate([imp, exp, c, d, cut]))), shape=(n, 6 * n))
    # storage: s_t - s_{t-1} - eta c_t + d_t / eta = 0  (s_{-1} = soc0)
    a2 = sparse.coo_matrix((np.concatenate([np.ones(n), -np.ones(n - 1), -eta * np.ones(n), np.ones(n) / eta]),
                            (np.concatenate([rows, rows[1:], rows, rows]),
                             np.concatenate([s, s[:-1], c, d]))), shape=(n, 6 * n))
    cost = np.concatenate([np.full(n, wear), np.full(n, wear), buy, -sell, np.zeros(n), np.zeros(n)])
    cost[s[-1]] -= terminal_value
    cap = np.full(n, np.inf) if cap is None else cap
    bounds = ([(0, step)] * (2 * n) + [(0, None)] * n + [(0, None if not np.isfinite(x) else x) for x in cap]
              + [(0, max(float(p), 0.0)) for p in pv] + [(b.soc_min * b.capacity_kwh, b.soc_max * b.capacity_kwh)] * n)
    res = linprog(cost, A_eq=sparse.vstack([a1, a2]).tocsc(), b_eq=np.concatenate([load - pv, np.r_[soc0, np.zeros(n - 1)]]),
                  bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(f"battery LP failed: {res.message}")
    x = res.x
    return x[c], x[d], x[exp]


def to_action(charge: float, discharge: float, surplus: float, cap: float, b: Battery) -> int:
    """The option that carries out a planned (charge, discharge) given the expected surplus; where a
    trip threatens the inverter keeps the export under the cap ("absorb_peak") at least."""
    step = b.power_kw * STEP_H
    capped = np.isfinite(cap)
    if charge < 1e-3 and discharge < 1e-3:
        return ACTIONS.index("absorb_peak" if capped else "idle")
    if charge >= discharge:
        if capped and charge <= max(surplus - cap, 0) + 0.1 * step:
            return ACTIONS.index("absorb_peak")
        if charge <= max(surplus, 0) + 0.15 * step:
            return ACTIONS.index("charge_surplus")
        return ACTIONS.index("charge_100" if charge > 0.75 * step else "charge_50" if charge > 0.375 * step else "charge_25")
    if discharge <= max(-surplus, 0) + 0.15 * step:
        return ACTIONS.index("cover_deficit")
    return ACTIONS.index("discharge_100" if discharge > 0.75 * step else
                         "discharge_50" if discharge > 0.375 * step else "discharge_25")


def lp_mpc(sc: Scenario, soc0: float | None = None, horizon_h: int = 36, every: int = 4) -> Result:
    """Re-plan every `every` quarters for the next `horizon_h` hours on the forecasts (linear program)."""
    b = sc.battery
    plan: dict[int, int] = {}

    def decide(t, soc):
        if t not in plan:
            n = min(horizon_h * 4, len(sc.load) - t)
            pv, load, buy, sell, cap = _forecast(sc, t, n)
            c, d, _ = plan_lp(load, pv, buy, sell, b, soc, _left_value(b, buy, sell), cap)
            for k in range(min(every, n)):
                plan[t + k] = to_action(c[k], d[k], pv[k] - load[k], cap[k], b)
        return plan[t]

    return run(sc, "lp_mpc", decide, soc0)


def with_tariff(sc: Scenario, **changes) -> Scenario:
    """The same scenario (same forecast errors) with tariff fields changed."""
    return dataclasses.replace(sc, tariff=dataclasses.replace(sc.tariff, **changes))


def calibrate(sc: Scenario, thetas=(0.0, 0.5, 1.0)) -> tuple[Scenario, dict[float, float]]:
    """The scenario with the tariff's `theta` that gives the lowest exact bill when the battery is
    run optimally: how much an exported kWh is really worth to this house over the year."""
    bills = {th: optimum(with_tariff(sc, theta=th)).settle(sc)["total_zl"] for th in thetas}
    best = min(bills, key=bills.get)
    return with_tariff(sc, theta=best), bills


__all__ = ["HORIZON", "STEPS_PER_DAY", "Result", "agent", "calibrate", "dp_mpc", "lp_mpc", "no_battery", "optimum",
           "plan_lp", "rule", "run", "to_action", "values", "with_tariff"]
