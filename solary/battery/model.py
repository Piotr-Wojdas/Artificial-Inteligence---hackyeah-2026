"""The house, the battery and the tariff: physics and money of one 15-minute step, the forecasts
the controller has at that moment, and the observation the trained agent gets.

Energy flows of one step (kWh, the inverter's AC bus):
    grid import - grid export = load - pv + charge - discharge
    stored energy += charge x efficiency - discharge / efficiency
Money of one step (zł): import x buy price - export x sell price + discharge x wear cost.

The controller decides at the start of each quarter. It knows the actual PV and load only of
the quarter that ended; what comes next it knows from forecasts:
  * prices: RCE as published (today's; after 14:00 also tomorrow's); beyond that the same
    quarter a day earlier ("persistence");
  * PV: the actual production with a forecast error that grows with the lead time (a weather
    forecast is good for the next hour and 20-40 % off for tomorrow on a single roof);
  * consumption: the expected profile (time of day, weekday, season).
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from .data import STEP_H, STEPS_PER_DAY, TZ

HORIZON = STEPS_PER_DAY              # the agent sees 24 h ahead
PUBLISH_HOUR = 14                    # PSE publishes tomorrow's RCE at about 14:00
# the controller's options each quarter (see `battery_flows`)
ACTIONS = ("self_consumption", "idle", "charge_surplus", "cover_deficit",
           "charge_half", "charge_full", "discharge_half", "discharge_full")


@dataclass(frozen=True)
class Battery:
    capacity_kwh: float = 10.0
    power_kw: float = 5.0
    efficiency: float = 0.95           # one way, so about 90 % round trip
    soc_min: float = 0.10
    soc_max: float = 1.0
    wear_zl_per_kwh: float = 0.10      # cost of cycling the battery, per kWh taken out


@dataclass(frozen=True)
class Tariff:
    """Prices for a prosumer in net-billing. Energy sent to the grid is worth RCE x sell_factor and
    goes to the prosumer deposit, which pays the ENERGY part of later bills (distribution fees are
    always paid). Deposit left after 12 months is refunded only up to `refund` of its value.

    How much one more kWh is worth therefore depends on the whole year: while the deposit is used
    up, an exported kWh is worth its full RCE and an imported one costs energy + distribution;
    once the deposit far exceeds the energy bought, an exported kWh is worth only `refund` x RCE
    and an imported one costs only distribution (the deposit pays the energy anyway). `theta`
    (0..1) places the house between these two cases; `buy` and `sell` are the resulting marginal
    prices the controller works with, `bill()` settles a year exactly and `strategies.calibrate`
    finds the theta with the lowest bill. The defaults are assumptions for 2026: check them
    against your own bill."""
    kind: str = "g11"                  # "g11": one price all day; "dynamic": exchange price every quarter
    g11_energy_zl_kwh: float = 0.62    # G11 energy, brutto
    distribution_zl_kwh: float = 0.38  # variable distribution fees, brutto
    margin_zl_kwh: float = 0.05        # dynamic: seller's margin on the exchange price, netto
    vat: float = 0.23
    sell_factor: float = 1.0           # exported energy is worth RCE x this (1.23 for some prosumers)
    refund: float = 0.30               # share of the deposit refunded after 12 months (RCE settlement)
    theta: float = 0.0                 # 0 = deposit used up, 1 = deposit far above the energy bought

    def energy(self, rce: np.ndarray) -> np.ndarray:
        if self.kind == "g11":
            return np.full(np.shape(rce), self.g11_energy_zl_kwh)
        if self.kind == "dynamic":
            return (np.asarray(rce) + self.margin_zl_kwh) * (1 + self.vat)
        raise ValueError(f"unknown tariff {self.kind!r}: use 'g11' or 'dynamic'")

    def deposit_value(self, rce: np.ndarray) -> np.ndarray:
        return np.maximum(np.asarray(rce), 0.0) * self.sell_factor     # negative RCE: worth 0

    def buy(self, rce: np.ndarray) -> np.ndarray:
        return self.distribution_zl_kwh + (1 - self.theta) * self.energy(rce)

    def sell(self, rce: np.ndarray) -> np.ndarray:
        return self.deposit_value(rce) * (1 - self.theta + self.theta * self.refund)


def bill(tariff: Tariff, rce: np.ndarray, grid_import: np.ndarray, grid_export: np.ndarray,
         wear_zl: float = 0.0) -> dict:
    """A year's bill settled by the net-billing rules (zł): distribution + energy - deposit used -
    refund of the unused deposit (at most `refund` of its value) + battery wear."""
    rce = _persist(np.asarray(rce, float))
    distribution = float((grid_import * tariff.distribution_zl_kwh).sum())
    energy = float((grid_import * tariff.energy(rce)).sum())
    deposit = float((grid_export * tariff.deposit_value(rce)).sum())
    used = min(deposit, energy)
    refunded = min(deposit - used, tariff.refund * deposit)
    return {"distribution_zl": distribution, "energy_zl": energy, "deposit_zl": deposit, "deposit_used_zl": used,
            "refund_zl": refunded, "lost_deposit_zl": deposit - used - refunded, "wear_zl": wear_zl,
            "total_zl": distribution + energy - used - refunded + wear_zl}


@dataclass
class Scenario:
    """One house over a stretch of time, as arrays on the 15-minute grid. Steps before `start`
    (at least a day) only feed the forecasts; the controller acts on steps start..end-1."""
    index: pd.DatetimeIndex
    rce: np.ndarray                    # zł/kWh netto; NaN = not published
    pv: np.ndarray                     # kWh per quarter, actual
    load: np.ndarray                   # kWh per quarter, actual
    load_expected: np.ndarray          # kWh per quarter, the forecast
    battery: Battery
    tariff: Tariff
    start: int = STEPS_PER_DAY
    end: int | None = None
    seed: int = 0
    pv_error_scale: float = 1.0        # 0 = perfect PV forecast
    known_rule: str = "publication"    # "publication" (history: 14:00 rule) or "nan" (live: what is published)
    buy: np.ndarray = field(init=False)
    sell: np.ndarray = field(init=False)

    def __post_init__(self):
        self.index = pd.DatetimeIndex(self.index).as_unit("ns")
        n = len(self.index)
        self.end = n - HORIZON if self.end is None else self.end
        rce = _persist(np.asarray(self.rce, float))     # gaps in history: the day before
        self.buy, self.sell = self.tariff.buy(rce), self.tariff.sell(rce)
        local = self.index.tz_convert(TZ)
        self.hour = (local.hour + local.minute / 60).to_numpy()
        self.doy = local.dayofyear.to_numpy()
        days = local.normalize()
        # first step whose price is NOT known at step t: next midnight, or the one after past 14:00
        ns = self.index.asi8
        nxt = np.searchsorted(ns, (days + pd.Timedelta(days=1)).tz_convert("UTC").as_unit("ns").asi8)
        nxt2 = np.searchsorted(ns, (days + pd.Timedelta(days=2)).tz_convert("UTC").as_unit("ns").asi8)
        self.known_until = np.where(self.hour >= PUBLISH_HOUR, nxt2, nxt)
        rng = np.random.default_rng(self.seed)
        day_no = days.as_unit("ns").asi8 // (86400 * 10**9)
        daily = rng.normal(0, 0.30, day_no.max() - day_no.min() + 1)[day_no - day_no.min()]
        ar = lfilter([1.0], [1.0, -0.9], rng.normal(0, 0.25 * math.sqrt(1 - 0.9 ** 2), n))   # AR(1), std 0.25
        self.pv_error = (daily + ar) * self.pv_error_scale
        self.rce_raw = np.asarray(self.rce, float)

    # ---------------------------------------------------------------- what is known at step t
    def price_forecast(self, t: int, n: int = HORIZON) -> np.ndarray:
        """RCE for steps t..t+n-1 as known at the start of step t."""
        j = np.arange(t, t + n)
        known = ~np.isnan(self.rce_raw[j])
        if self.known_rule == "publication":
            known &= j < self.known_until[t]
        out = np.where(known, self.rce_raw[j], np.nan)
        for back in (STEPS_PER_DAY, 2 * STEPS_PER_DAY, 7 * STEPS_PER_DAY):   # persistence
            miss = np.isnan(out) & (j - back >= 0)
            out[miss] = self.rce_raw[j[miss] - back]
        return np.nan_to_num(out, nan=float(np.nanmean(self.rce_raw[max(0, t - 7 * STEPS_PER_DAY):t + 1])))

    def pv_forecast(self, t: int, n: int = HORIZON) -> np.ndarray:
        """PV for steps t..t+n-1 as forecast at the start of step t."""
        lead = np.arange(n)
        weight = np.minimum(1.0, (lead + 1) / 16)               # full error from 4 h ahead
        sigma = 0.39 * self.pv_error_scale                       # std of pv_error: daily 0.30 + hourly 0.25
        return self.pv[t:t + n] * np.exp(weight * self.pv_error[t:t + n] - 0.5 * (weight * sigma) ** 2)  # unbiased


def _persist(values: np.ndarray) -> np.ndarray:
    """Missing prices filled with the same quarter a day (two days, a week) earlier."""
    out = values.copy()
    for back in (STEPS_PER_DAY, 2 * STEPS_PER_DAY, 7 * STEPS_PER_DAY):
        miss = np.isnan(out)
        if not miss.any():
            break
        idx = np.flatnonzero(miss)
        ok = idx - back >= 0
        out[idx[ok]] = out[idx[ok] - back]
    if np.isnan(out).any():
        out = np.where(np.isnan(out), np.nanmean(out) if np.isfinite(out).any() else 0.5, out)
    return out


# ------------------------------------------------------------------ physics
def battery_flows(action: int, soc_kwh: float, pv: float, load: float, b: Battery) -> tuple[float, float]:
    """(charge, discharge) in kWh on the AC side for one quarter. The first four options react to
    the quarter's actual surplus or deficit, as a hybrid inverter does; the last four are fixed
    power, from or to the grid when needed."""
    room = max(b.soc_max * b.capacity_kwh - soc_kwh, 0.0) / b.efficiency      # AC kWh it can still take
    avail = max(soc_kwh - b.soc_min * b.capacity_kwh, 0.0) * b.efficiency     # AC kWh it can still give
    step = b.power_kw * STEP_H
    surplus = pv - load
    name = ACTIONS[action]
    charge = discharge = 0.0
    if name in ("self_consumption", "charge_surplus") and surplus > 0:
        charge = min(surplus, step, room)
    if name in ("self_consumption", "cover_deficit") and surplus < 0:
        discharge = min(-surplus, step, avail)
    if name == "charge_half":
        charge = min(step / 2, room)
    elif name == "charge_full":
        charge = min(step, room)
    elif name == "discharge_half":
        discharge = min(step / 2, avail)
    elif name == "discharge_full":
        discharge = min(step, avail)
    return charge, discharge


def apply(sc: Scenario, t: int, soc_kwh: float, charge: float, discharge: float) -> tuple[float, float, dict]:
    """Run step t with the given AC charge / discharge: (new stored kWh, cost in zł, flows)."""
    b = sc.battery
    charge = min(max(charge, 0.0), max(b.soc_max * b.capacity_kwh - soc_kwh, 0.0) / b.efficiency)
    discharge = min(max(discharge, 0.0), max(soc_kwh - b.soc_min * b.capacity_kwh, 0.0) * b.efficiency)
    soc_kwh = soc_kwh + charge * b.efficiency - discharge / b.efficiency
    net = sc.load[t] - sc.pv[t] + charge - discharge
    imp, exp = max(net, 0.0), max(-net, 0.0)
    cost = imp * sc.buy[t] - exp * sc.sell[t] + discharge * b.wear_zl_per_kwh
    return soc_kwh, cost, {"import": imp, "export": exp, "charge": charge, "discharge": discharge}


def cost_without_battery(sc: Scenario, t0: int | None = None, t1: int | None = None) -> np.ndarray:
    s = slice(sc.start if t0 is None else t0, sc.end if t1 is None else t1)
    net = sc.load[s] - sc.pv[s]
    return np.maximum(net, 0) * sc.buy[s] - np.maximum(-net, 0) * sc.sell[s]


# ------------------------------------------------------------------ what the agent sees
OBS_SIZE = 1 + 4 + 2 + 4 * 24 + 2 + 1


def observe(sc: Scenario, t: int, soc_kwh: float) -> np.ndarray:
    """The agent's view at the start of step t; energies relative to the battery's capacity."""
    b = sc.battery
    cap = b.capacity_kwh
    rce = sc.price_forecast(t)
    buy, sell = sc.tariff.buy(rce), sc.tariff.sell(rce)
    pv = sc.pv_forecast(t)
    load = sc.load_expected[t:t + HORIZON]
    hourly = lambda v: v.reshape(24, 4).mean(axis=1)        # noqa: E731
    h, d = sc.hour[t], sc.doy[t]
    return np.concatenate([
        [soc_kwh / cap],
        [math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24),
         math.sin(2 * math.pi * d / 365.25), math.cos(2 * math.pi * d / 365.25)],
        [buy[0], sell[0]],
        hourly(buy), hourly(sell),
        hourly(pv) * 4 / cap, hourly(load) * 4 / cap,          # kWh per hour / capacity
        [sc.pv[t - 1] * 4 / cap, sc.load[t - 1] * 4 / cap],
        [b.power_kw / cap],
    ]).astype(np.float32)
