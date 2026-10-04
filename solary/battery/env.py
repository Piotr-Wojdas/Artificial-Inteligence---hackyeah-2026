"""Gymnasium environment for training the agent: a random house, a random week of real prices and
weather, every quarter one of model.ACTIONS. Reward: zł saved in the quarter compared with the
same house without a battery (the same optimum as minimising the bill, with less noise).

Each episode draws a different house, so one policy works for many: PV size and orientation, the
battery's size, power, losses and wear, the yearly consumption, the tariff (G11 or dynamic) and its
prices, and the local grid (strong, or tripping the inverter above an export limit when it is full
of PV). Needs the "rl" dependency group (gymnasium)."""

from __future__ import annotations

from dataclasses import dataclass

import gymnasium as gym
import numpy as np
import pandas as pd

from .data import STEPS_PER_DAY, Inputs, load_profile
from .model import (ACTIONS, HORIZON, OBS_SIZE, Battery, Grid, Scenario, Tariff, cost_without_battery,
                    forecast_uncertainty, observe, step_action)

REWARD_SCALE = 10.0
# PV plane sets (tilt, compass azimuth, share of kWp) the episodes draw from
ORIENTATIONS = {
    "S35": [(35, 180, 1.0)], "SE35": [(35, 135, 1.0)], "SW35": [(35, 225, 1.0)],
    "EW30": [(30, 90, 0.5), (30, 270, 0.5)], "E35": [(35, 90, 1.0)], "W35": [(35, 270, 1.0)],
    "flat10": [(10, 180, 1.0)],
}


@dataclass
class Place:
    """Prices, weather, PV per kWp and its forecast uncertainty for every orientation at one location."""
    inputs: Inputs
    pv_per_kwp: dict[str, np.ndarray]
    sigma: dict[str, np.ndarray]

    @classmethod
    def build(cls, inputs: Inputs) -> "Place":
        pv = {name: inputs.pv([(share, tilt, az) for tilt, az, share in planes]) for name, planes in ORIENTATIONS.items()}
        days = inputs.index.tz_convert("Europe/Warsaw").normalize().as_unit("ns").asi8 // (86400 * 10**9)
        return cls(inputs, pv, {name: forecast_uncertainty(v, days) for name, v in pv.items()})


def random_house(rng: np.random.Generator) -> dict:
    capacity, kwp = float(rng.uniform(5, 20)), float(rng.uniform(3, 12))
    return {
        "kwp": kwp,
        "orientation": str(rng.choice(list(ORIENTATIONS))),
        "annual_kwh": float(rng.uniform(2000, 7000)),
        "battery": Battery(capacity_kwh=capacity, power_kw=capacity * float(rng.uniform(0.3, 1.0)),
                           efficiency=float(rng.uniform(0.965, 0.985)), standby_kw=float(rng.uniform(0.02, 0.06)),
                           resistive=float(rng.uniform(0.02, 0.05)), wear_zl_per_kwh=float(rng.uniform(0.05, 0.15)),
                           crate_wear=float(rng.uniform(0.2, 0.6)), high_soc_zl_per_h=float(rng.uniform(0.005, 0.02))),
        "tariff": Tariff(kind=str(rng.choice(["g11", "dynamic"])), g11_energy_zl_kwh=float(rng.uniform(0.5, 0.75)),
                         distribution_zl_kwh=float(rng.uniform(0.3, 0.45)), margin_zl_kwh=float(rng.uniform(0, 0.1)),
                         sell_factor=float(rng.choice([1.0, 1.23], p=[0.7, 0.3])),
                         theta=float(rng.choice([0.0, rng.uniform(0, 1), 1.0]))),
        "grid": Grid(export_limit_kw=None if rng.random() < 0.35 else kwp * float(rng.uniform(0.3, 0.8)),
                     trip_price=float(rng.uniform(0.05, 0.25))),
    }


def scenario(place: Place, first_step: int, days: int, house: dict, seed: int) -> Scenario:
    """`days` days of control starting at `first_step` (a local midnight), with a day before for
    the forecasts and a horizon after."""
    lo, hi = first_step - STEPS_PER_DAY, first_step + days * STEPS_PER_DAY + HORIZON
    inp = place.inputs
    index = inp.index[lo:hi]
    load, expected = load_profile(index, house["annual_kwh"], seed=seed)
    o = house["orientation"]
    return Scenario(index=index, rce=inp.rce[lo:hi], pv=place.pv_per_kwp[o][lo:hi] * house["kwp"], load=load,
                    load_expected=expected, battery=house["battery"], tariff=house["tariff"], grid=house["grid"],
                    start=STEPS_PER_DAY, end=STEPS_PER_DAY + days * STEPS_PER_DAY, seed=seed,
                    sigma_in=place.sigma[o][lo:hi])


class BatteryEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, places: list[Place], days: int = 7, seed: int | None = None):
        self.places, self.days = places, days
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, (OBS_SIZE,), np.float32)
        self.action_space = gym.spaces.Discrete(len(ACTIONS))
        self.rng = np.random.default_rng(seed)

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        place = self.places[int(self.rng.integers(len(self.places)))]
        first = int(self.rng.choice(_midnights(place.inputs.index, self.days)))
        self.house = random_house(self.rng)
        self.sc = scenario(place, first, self.days, self.house, seed=int(self.rng.integers(2**31)))
        self.baseline = cost_without_battery(self.sc)
        b = self.sc.battery
        self.soc = float(self.rng.uniform(b.soc_min, b.soc_max)) * b.capacity_kwh
        self.t = self.sc.start
        return observe(self.sc, self.t, self.soc), {}

    def step(self, action: int):
        sc, t = self.sc, self.t
        self.soc, cost, _ = step_action(sc, t, self.soc, int(action))
        reward = (self.baseline[t - sc.start] - cost) * REWARD_SCALE
        self.t += 1
        done = self.t >= sc.end
        return observe(sc, self.t if not done else self.t - 1, self.soc), float(reward), done, False, {}


def _midnights(index: pd.DatetimeIndex, days: int) -> np.ndarray:
    """Steps that start a local day and leave room for a day before and `days` days + horizon after."""
    local = index.tz_convert("Europe/Warsaw")
    starts = np.flatnonzero((local.hour == 0) & (local.minute == 0))
    return starts[(starts >= STEPS_PER_DAY) & (starts + days * STEPS_PER_DAY + HORIZON <= len(index))]
