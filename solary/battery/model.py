"""The house, the battery and the grid: physics and money of one 15-minute step, the forecasts the
controller has at that moment, and the observation the trained agent gets.

The physics is deliberately not linear, because real installations are not:
  * Inverter losses depend on power. A hybrid inverter burns `standby_kw` whenever it moves battery
    energy, plus resistive losses growing with the square of the power: covering a 200 W night load
    from the battery loses ~20 %, charging at full power ~4 %.
  * Battery wear depends on how it is used: fast charging or discharging (high C-rate) wears more per
    kWh, and so does sitting above 90 % charge (calendar ageing).
  * Overvoltage trips. On sunny middays the local grid is full of PV (the market price RCE is then
    low) and its voltage rises with every kW exported. Above `export_limit_kw` the inverter's
    protection disconnects it (EN 50549 / 253 V, a common problem in Poland): the quarter's PV is lost
    and the house runs on the grid.
  * Import and export never happen in the same quarter, even when RCE is above the purchase price.
A linear program (strategies.lp_mpc) can only approximate all of this; dynamic programming over the
state of charge (strategies.optimum, strategies.dp_mpc) and the trained agent use it exactly.

Energy of one step (kWh on the inverter's AC side):
    grid import - grid export = load - pv + charge - discharge
    stored += (charge - loss(charge)) x efficiency - (discharge + loss(discharge)) / efficiency
Money of one step (zł): import x buy - export x sell + wear + ageing at high charge.

The controller decides at the start of each quarter. It knows the actual PV and load only of the
quarter that ended (the reactive options follow the current quarter as a hybrid inverter does);
what comes next it knows from forecasts:
  * prices: RCE as published (today's; after 14:00 also tomorrow's), beyond that the same quarter a
    day earlier ("persistence");
  * PV: the actual production with a forecast error whose size depends on the weather (clear and
    overcast days are easy, partly cloudy ones hard) and grows with the lead time. The controller
    knows how uncertain each day is (as an ensemble forecast's spread would tell it);
  * consumption: the expected profile (time of day, weekday, season).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from scipy.ndimage import maximum_filter1d
from scipy.signal import lfilter

from .data import STEP_H, STEPS_PER_DAY, TZ

HORIZON = 144                        # the agent looks 36 h ahead (prices are published up to 34 h ahead)
PUBLISH_HOUR = 14                    # PSE publishes tomorrow's RCE at about 14:00
HIGH_SOC = 0.9                       # above this the battery ages faster
# the controller's options each quarter (see `battery_flows`)
ACTIONS = ("self_consumption", "idle", "charge_surplus", "absorb_peak", "cover_deficit",
           "charge_25", "charge_50", "charge_100", "discharge_25", "discharge_50", "discharge_100")
_CHARGE_SHARE = {"charge_25": 0.25, "charge_50": 0.5, "charge_100": 1.0}
_DISCHARGE_SHARE = {"discharge_25": 0.25, "discharge_50": 0.5, "discharge_100": 1.0}


@dataclass(frozen=True)
class Battery:
    capacity_kwh: float = 10.0
    power_kw: float = 5.0
    efficiency: float = 0.975          # cells, one way (the inverter's losses are below)
    soc_min: float = 0.10
    soc_max: float = 1.0
    standby_kw: float = 0.04           # inverter's own consumption while it moves battery energy
    resistive: float = 0.03            # extra loss at full power (share of the power), grows with power^2
    wear_zl_per_kwh: float = 0.10      # wear per kWh cycled at moderate power
    crate_wear: float = 0.4            # wear x (1 - c + 2c (power / rated)^2): fast cycling wears more
    high_soc_zl_per_h: float = 0.01    # ageing while kept full, per hour at 100 % (0 below 90 %)

    @classmethod
    def linear(cls, capacity_kwh: float = 10.0, power_kw: float = 5.0, **kw) -> "Battery":
        """The textbook battery: constant 95 % efficiency, linear wear, no ageing at high charge."""
        return cls(capacity_kwh, power_kw, efficiency=0.95, standby_kw=0.0, resistive=0.0, crate_wear=0.0,
                   high_soc_zl_per_h=0.0, **kw)

    def one_way_efficiency(self, share: float = 0.5) -> float:
        """Cells x inverter at `share` of the rated power: what a linear model would assume."""
        p = share * self.power_kw
        return self.efficiency * (1 - (self.standby_kw + self.resistive * p * p / self.power_kw) / p)


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


@dataclass(frozen=True)
class Grid:
    """The local grid. `export_limit_kw` None = a strong grid that never trips the inverter."""
    export_limit_kw: float | None = None
    trip_price: float = 0.15           # RCE (zł/kWh) at or below which local PV pushes the voltage up


def bill(tariff: Tariff, rce: np.ndarray, grid_import: np.ndarray, grid_export: np.ndarray,
         wear_zl: float = 0.0) -> dict:
    """A year's bill settled by the net-billing rules (zł): distribution + energy - deposit used -
    refund of the unused deposit (at most `refund` of its value) + battery wear and ageing."""
    rce = _persist(np.asarray(rce, float))
    distribution = float((grid_import * tariff.distribution_zl_kwh).sum())
    energy = float((grid_import * tariff.energy(rce)).sum())
    deposit = float((grid_export * tariff.deposit_value(rce)).sum())
    used = min(deposit, energy)
    refunded = min(deposit - used, tariff.refund * deposit)
    return {"distribution_zl": distribution, "energy_zl": energy, "deposit_zl": deposit, "deposit_used_zl": used,
            "refund_zl": refunded, "lost_deposit_zl": deposit - used - refunded, "wear_zl": wear_zl,
            "total_zl": distribution + energy - used - refunded + wear_zl}


def forecast_uncertainty(pv: np.ndarray, days: np.ndarray) -> np.ndarray:
    """Per step: the typical relative error of a day-ahead PV forecast for that step's day. Clear
    days (production near the best of the surrounding weeks) and overcast days are easy; partly
    cloudy ones are hard."""
    uniq, inv = np.unique(days, return_inverse=True)
    daily = np.bincount(inv, weights=pv, minlength=len(uniq))
    clear = maximum_filter1d(daily, size=21, mode="nearest")
    k = np.where(clear > 1e-6, daily / np.maximum(clear, 1e-6), 1.0)
    sigma = np.clip(0.08 + 1.6 * k * (1 - k), 0.08, 0.5)
    return sigma[inv]


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
    grid: Grid = Grid()
    start: int = STEPS_PER_DAY
    end: int | None = None
    seed: int = 0
    pv_error_scale: float = 1.0        # 0 = perfect PV forecast
    known_rule: str = "publication"    # "publication" (history: 14:00 rule) or "nan" (live: what is published)
    sigma_in: np.ndarray | None = None  # forecast uncertainty per step, if known from a longer series
    buy: np.ndarray = field(init=False)
    sell: np.ndarray = field(init=False)

    def __post_init__(self):
        self.index = pd.DatetimeIndex(self.index).as_unit("ns")
        n = len(self.index)
        self.end = n - HORIZON if self.end is None else self.end
        self.rce_raw = np.asarray(self.rce, float)
        self.rce_filled = _persist(self.rce_raw)        # gaps in history: the day before
        self.buy, self.sell = self.tariff.buy(self.rce_filled), self.tariff.sell(self.rce_filled)
        local = self.index.tz_convert(TZ)
        self.hour = np.asarray(local.hour + local.minute / 60, float)
        self.doy = np.asarray(local.dayofyear)
        days = local.normalize()
        ns = self.index.asi8
        # first step whose price is NOT known at step t: next midnight, or the one after past 14:00
        nxt = np.searchsorted(ns, (days + pd.Timedelta(days=1)).tz_convert("UTC").as_unit("ns").asi8)
        nxt2 = np.searchsorted(ns, (days + pd.Timedelta(days=2)).tz_convert("UTC").as_unit("ns").asi8)
        self.known_until = np.where(self.hour >= PUBLISH_HOUR, nxt2, nxt)
        self.day_no = days.as_unit("ns").asi8 // (86400 * 10**9)
        self.sigma = forecast_uncertainty(self.pv, self.day_no) if self.sigma_in is None else np.asarray(self.sigma_in)
        rng = np.random.default_rng(self.seed)
        first = self.day_no.min()
        daily = rng.normal(0, 1, self.day_no.max() - first + 1)[self.day_no - first]
        ar = lfilter([1.0], [1.0, -0.9], rng.normal(0, math.sqrt(1 - 0.9 ** 2), n))      # AR(1), unit std
        self.pv_error = self.sigma * (daily + 0.8 * ar) * self.pv_error_scale
        self.cap = self.trip_cap(self.rce_filled)

    def trip_cap(self, rce: np.ndarray) -> np.ndarray:
        """kWh per quarter the house may export before the inverter trips (inf = no risk)."""
        g = self.grid
        if g.export_limit_kw is None:
            return np.full(np.shape(rce), np.inf)
        return np.where(np.asarray(rce) <= g.trip_price, g.export_limit_kw * STEP_H, np.inf)

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

    def known_steps(self, t: int) -> int:
        """How many of the next steps have published prices."""
        if self.known_rule == "publication":
            return int(self.known_until[t] - t)
        ahead = ~np.isnan(self.rce_raw[t:t + HORIZON])
        return int(np.argmin(ahead)) if not ahead.all() else HORIZON

    def pv_forecast(self, t: int, n: int = HORIZON) -> np.ndarray:
        """PV for steps t..t+n-1 as forecast at the start of step t (unbiased)."""
        weight = np.minimum(1.0, (np.arange(n) + 1) / 16)        # full error from 4 h ahead
        sigma = self.sigma[t:t + n] * 1.28 * self.pv_error_scale  # std of pv_error
        return self.pv[t:t + n] * np.exp(weight * self.pv_error[t:t + n] - 0.5 * (weight * sigma) ** 2)


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


# ------------------------------------------------------------------ physics (scalars or arrays)
def inverter_loss(e, b: Battery):
    """kWh lost in the inverter moving `e` AC kWh in a quarter."""
    e = np.asarray(e, float)
    return np.where(e > 1e-9, (b.standby_kw + b.resistive * (e / STEP_H) ** 2 / b.power_kw) * STEP_H, 0.0)


def max_charge(room_dc, b: Battery):
    """Most AC kWh that fit in `room_dc` stored kWh, within the power limit."""
    need = np.asarray(room_dc, float) / b.efficiency
    s, a, step = b.standby_kw * STEP_H, b.resistive / (b.power_kw * STEP_H), b.power_kw * STEP_H
    if a == 0:
        e = np.where(need > 1e-9, need + s, 0.0)
    else:
        disc = 1 - 4 * a * (s + need)
        e = np.where(disc >= 0, (1 - np.sqrt(np.maximum(disc, 0))) / (2 * a), np.inf)
        e = np.where(need > 1e-9, e, 0.0)
    return np.minimum(e, step)


def max_discharge(avail_dc, b: Battery):
    """Most AC kWh the battery can deliver from `avail_dc` stored kWh, within the power limit."""
    have = np.asarray(avail_dc, float) * b.efficiency - b.standby_kw * STEP_H
    a, step = b.resistive / (b.power_kw * STEP_H), b.power_kw * STEP_H
    if a == 0:
        e = np.maximum(have, 0.0)
    else:
        e = np.where(have > 0, (-1 + np.sqrt(1 + 4 * a * np.maximum(have, 0))) / (2 * a), 0.0)
    return np.minimum(e, step)


def battery_flows(action, soc_kwh, pv, load, b: Battery, cap=np.inf):
    """(charge, discharge) in AC kWh for one quarter. The first five options react to the quarter's
    actual surplus or deficit, as a hybrid inverter does (`absorb_peak` stores only what would push
    the export above `cap`, the kWh allowed before a trip); the rest are fixed power, from or to the
    grid when needed. Every argument may be a numpy array (they broadcast)."""
    action = np.asarray(action)
    soc_kwh = np.asarray(soc_kwh, float)
    cmax = max_charge(np.maximum(b.soc_max * b.capacity_kwh - soc_kwh, 0.0), b)
    dmax = max_discharge(np.maximum(soc_kwh - b.soc_min * b.capacity_kwh, 0.0), b)
    step = b.power_kw * STEP_H
    surplus = np.asarray(pv, float) - np.asarray(load, float)
    over, under = np.maximum(surplus, 0.0), np.maximum(-surplus, 0.0)
    peak = np.maximum(over - np.minimum(cap, 1e9), 0.0)
    zero = np.zeros_like(over)
    want_charge = np.choose(action, [over, zero, over, peak, zero, zero + 0.25 * step, zero + 0.5 * step,
                                     zero + step, zero, zero, zero])
    want_discharge = np.choose(action, [under, zero, zero, zero, under, zero, zero, zero, zero + 0.25 * step,
                                        zero + 0.5 * step, zero + step])
    return np.minimum(want_charge, cmax), np.minimum(want_discharge, dmax)


def transition(b: Battery, soc_kwh, charge, discharge, pv, load, buy, sell, cap):
    """One quarter: (new stored kWh, cost in zł, flows). Arrays broadcast; `cap` is the export (kWh)
    above which the inverter trips: then PV and battery are off and the house imports its load."""
    soc_kwh = np.asarray(soc_kwh, float)
    charge, discharge = np.asarray(charge, float), np.asarray(discharge, float)
    stored = np.maximum(charge - inverter_loss(charge, b), 0.0) * b.efficiency
    taken = (discharge + inverter_loss(discharge, b)) / b.efficiency
    net = load - pv + charge - discharge
    exp = np.maximum(-net, 0.0)
    trip = exp > cap + 1e-9
    imp = np.where(trip, load, np.maximum(net, 0.0))
    exp = np.where(trip, 0.0, exp)
    stored, taken = np.where(trip, 0.0, stored), np.where(trip, 0.0, taken)
    new = soc_kwh + stored - taken
    share_c = np.where(trip, 0.0, charge) / STEP_H / b.power_kw
    share_d = np.where(trip, 0.0, discharge) / STEP_H / b.power_kw
    c = b.crate_wear
    wear = 0.5 * b.wear_zl_per_kwh * (stored * (1 - c + 2 * c * share_c ** 2) + taken * (1 - c + 2 * c * share_d ** 2))
    ageing = b.high_soc_zl_per_h * STEP_H * np.maximum(new / b.capacity_kwh - HIGH_SOC, 0.0) / (1 - HIGH_SOC)
    cost = imp * buy - exp * sell + wear + ageing
    return new, cost, {"import": imp, "export": exp, "charge": np.where(trip, 0.0, charge),
                       "discharge": np.where(trip, 0.0, discharge), "trip": trip,
                       "lost_pv": np.where(trip, pv, 0.0), "wear": wear + ageing}


def apply(sc: Scenario, t: int, soc_kwh: float, charge: float, discharge: float) -> tuple[float, float, dict]:
    """Run step t with the given AC charge / discharge: (new stored kWh, cost in zł, flows)."""
    b = sc.battery
    charge = float(min(max(charge, 0.0), max_charge(max(b.soc_max * b.capacity_kwh - soc_kwh, 0.0), b)))
    discharge = float(min(max(discharge, 0.0), max_discharge(max(soc_kwh - b.soc_min * b.capacity_kwh, 0.0), b)))
    new, cost, f = transition(b, soc_kwh, charge, discharge, sc.pv[t], sc.load[t], sc.buy[t], sc.sell[t], sc.cap[t])
    return float(new), float(cost), {k: (bool(v) if k == "trip" else float(v)) for k, v in f.items()}


def step_action(sc: Scenario, t: int, soc_kwh: float, action: int) -> tuple[float, float, dict]:
    """Run step t with one of ACTIONS (reacting to the quarter's actual PV and load)."""
    c, d = battery_flows(action, soc_kwh, sc.pv[t], sc.load[t], sc.battery, sc.cap[t])
    return apply(sc, t, soc_kwh, float(c), float(d))


def cost_without_battery(sc: Scenario, t0: int | None = None, t1: int | None = None) -> np.ndarray:
    s = slice(sc.start if t0 is None else t0, sc.end if t1 is None else t1)
    _, cost, _ = transition(sc.battery, 0.0, 0.0, 0.0, sc.pv[s], sc.load[s], sc.buy[s], sc.sell[s], sc.cap[s])
    return cost


# ------------------------------------------------------------------ what the agent sees
NEAR, FAR = 16, 32                   # 15-min steps for the next 4 h, then hourly up to 36 h
OBS_SIZE = 1 + 4 + 2 + 4 * (NEAR + FAR) + NEAR + 1 + 2 + 1 + 2 + 5


def _profile(v: np.ndarray) -> np.ndarray:
    """A 36 h series as 16 quarters, then 32 hourly means."""
    return np.concatenate([v[:NEAR], v[NEAR:NEAR + 4 * FAR].reshape(FAR, 4).mean(axis=1)])


def observe(sc: Scenario, t: int, soc_kwh: float) -> np.ndarray:
    """The agent's view at the start of step t; energies as kW relative to the battery's capacity."""
    b = sc.battery
    cap = b.capacity_kwh
    rce = sc.price_forecast(t)
    buy, sell = sc.tariff.buy(rce), sc.tariff.sell(rce)
    pv = sc.pv_forecast(t)
    load = sc.load_expected[t:t + HORIZON]
    limit = sc.trip_cap(rce[:NEAR]) < np.inf
    h, d = sc.hour[t], sc.doy[t]
    today = sc.sigma[t]
    tomorrow = sc.sigma[min(t + STEPS_PER_DAY, len(sc.sigma) - 1)]
    export_limit = sc.grid.export_limit_kw if sc.grid.export_limit_kw is not None else 0.0
    return np.concatenate([
        [soc_kwh / cap],
        [math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24),
         math.sin(2 * math.pi * d / 365.25), math.cos(2 * math.pi * d / 365.25)],
        [buy[0], sell[0]],
        _profile(buy), _profile(sell),
        _profile(pv) * 4 / cap, _profile(load) * 4 / cap,
        limit.astype(float), [export_limit / cap],
        [today, tomorrow], [min(sc.known_steps(t), HORIZON) / HORIZON],
        [sc.pv[t - 1] * 4 / cap, sc.load[t - 1] * 4 / cap],
        [b.power_kw / cap, b.standby_kw / b.power_kw, b.resistive, b.wear_zl_per_kwh, b.high_soc_zl_per_h * 10],
    ]).astype(np.float32)


__all__ = ["ACTIONS", "Battery", "Grid", "HORIZON", "OBS_SIZE", "Scenario", "Tariff", "apply", "battery_flows",
           "bill", "cost_without_battery", "observe", "replace", "step_action", "transition"]
