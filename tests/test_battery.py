"""Battery controller on synthetic days (no PSE, NASA or Open-Meteo involved)."""

import dataclasses
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from solary.battery import data as D
from solary.battery import model as M
from solary.battery import strategies as S
from solary.battery.policy import POLICY_PATH, Policy


def synthetic_inputs(days: int = 6, first: date = date(2025, 6, 2)) -> D.Inputs:
    """Clear days, prices low at noon and high in the evening (like RCE), in Katowice."""
    lo, hi = D.local_day_bounds(first, first + pd.Timedelta(days=days - 1))
    index = D.quarters(lo, hi)
    local = index.tz_convert(D.TZ)
    h = np.asarray(local.hour + local.minute / 60 + 0.125)
    rce = 0.45 - 0.35 * np.exp(-((h - 13) / 2.5) ** 2) + 0.55 * np.exp(-((h - 19.5) / 1.5) ** 2)
    zenith, _ = D.solar_position(index + pd.Timedelta(minutes=7.5), 50.26, 19.02)
    ghi = np.clip(950 * np.cos(np.radians(zenith)), 0, None)
    weather = pd.DataFrame({"ghi": ghi, "dhi": 0.2 * ghi, "temp": np.full(len(index), 20.0)}, index=index)
    return D.Inputs(index, rce, weather, 50.26, 19.02)


def make_scenario(tariff=M.Tariff(), battery=M.Battery(), kwp=6.0, days=7, error=1.0, grid=M.Grid()) -> M.Scenario:
    """Days of control: `days` minus the day before and the 36 h horizon."""
    inp = synthetic_inputs(days)
    load, expected = D.load_profile(inp.index, 4000, seed=3)
    return M.Scenario(index=inp.index, rce=inp.rce, pv=inp.pv([(kwp, 35, 180)]), load=load, load_expected=expected,
                      battery=battery, tariff=tariff, grid=grid, seed=5, pv_error_scale=error)


WEAK = M.Grid(export_limit_kw=2.5, trip_price=0.15)   # the synthetic noon price dips to 0.10


# ------------------------------------------------------------------ data
def test_sun_and_pv():
    noon = pd.DatetimeIndex([pd.Timestamp("2025-06-21 10:45", tz="UTC")])   # solar noon in Katowice
    zenith, azimuth = D.solar_position(noon, 50.26, 19.02)
    assert zenith[0] == pytest.approx(90 - (90 - 50.26 + 23.44), abs=0.5) and azimuth[0] == pytest.approx(180, abs=3)
    inp = synthetic_inputs(1)
    pv = inp.pv([(1.0, 35, 180)])
    local = inp.index.tz_convert(D.TZ)
    assert pv[np.asarray(local.hour) < 3].sum() == 0 and pv.sum() > 4          # nothing at night, ~5 kWh/kWp on a clear June day
    east = inp.pv([(1.0, 35, 90)])
    morning = np.asarray(local.hour) < 10
    assert east[morning].sum() > pv[morning].sum()                             # east roofs produce earlier


def test_load_profile_matches_the_yearly_total():
    index = D.quarters(*D.local_day_bounds(date(2025, 1, 1), date(2025, 12, 31)))
    actual, expected = D.load_profile(index, 4000, seed=1)
    assert expected.sum() == pytest.approx(4000, rel=0.02) and actual.sum() == pytest.approx(4000, rel=0.05)
    local = index.tz_convert(D.TZ)
    hour = np.asarray(local.hour)
    assert expected[hour == 19].mean() > 2 * expected[hour == 3].mean()       # evening peak, quiet night


def test_hourly_weather_to_quarters():
    hourly = pd.DataFrame({"ghi": [0.0, 400.0, 800.0]},
                          index=pd.date_range("2025-06-01 08:00", periods=3, freq="h", tz="UTC").as_unit("ns"))
    q = D.to_quarters(hourly, D.quarters(pd.Timestamp("2025-06-01 08:00", tz="UTC"), pd.Timestamp("2025-06-01 11:00", tz="UTC")))
    assert q["ghi"].iloc[0] == 0.0                                             # before the first hour's middle
    assert q["ghi"].iloc[2] == pytest.approx(400 * 7.5 / 60)                   # 08:37.5, just past 08:30
    assert q["ghi"].iloc[6] == pytest.approx(400 + 400 * 7.5 / 60)             # 09:37.5


def test_rce_pages_and_cache(cfg, monkeypatch):
    calls = []
    pages = {None: {"value": [{"dtime_utc": "2025-01-01 00:15:00", "rce_pln": 500.0}], "nextLink": "https://next"},
             "https://next": {"value": [{"dtime_utc": "2025-01-01 00:30:00", "rce_pln": -20.0}]}}

    def get(url, params=None, timeout=60, attempts=3):
        calls.append(url)
        return pages[None if url == D.PSE_URL else url]

    monkeypatch.setattr(D, "_get_json", get)
    s = D.rce(date(2025, 1, 1), date(2025, 1, 1), cfg)
    assert list(s.values) == [0.5, -0.02]                                      # zł/MWh -> zł/kWh
    assert s.index[0] == pd.Timestamp("2025-01-01 00:00", tz="UTC")            # labelled by the quarter's start
    D.rce(date(2025, 1, 1), date(2025, 1, 1), cfg)
    assert len(calls) == 2                                                     # a finished month comes from disk


# ------------------------------------------------------------------ physics and money
def test_inverter_losses_depend_on_power():
    b = M.Battery()
    eff = lambda kw: 1 - float(M.inverter_loss(kw * D.STEP_H, b)) / (kw * D.STEP_H)   # noqa: E731
    assert eff(0.2) < 0.85 < 0.95 < eff(2.5)                                    # a night load wastes, mid power does not
    assert eff(5.0) < eff(2.5)                                                  # resistive losses at full power
    assert float(M.inverter_loss(0.0, b)) == 0.0                                # an idle battery costs nothing
    lin = M.Battery.linear()
    assert float(M.inverter_loss(0.05, lin)) == 0.0 and lin.one_way_efficiency() == pytest.approx(0.95)


def test_battery_options():
    b = M.Battery(capacity_kwh=10, power_kw=4)
    a = {name: i for i, name in enumerate(M.ACTIONS)}
    flows = lambda name, soc, pv, load, cap=np.inf: tuple(float(x) for x in M.battery_flows(a[name], soc, pv, load, b, cap))  # noqa: E731
    assert flows("self_consumption", 5.0, 2.0, 0.5) == (1.0, 0.0)               # 4 kW x 15 min
    assert flows("self_consumption", 5.0, 0.0, 0.3) == (0.0, pytest.approx(0.3))
    assert flows("charge_surplus", 5.0, 0.0, 0.3) == (0.0, 0.0)
    assert flows("cover_deficit", 5.0, 2.0, 0.5) == (0.0, 0.0)                  # sells the surplus
    assert flows("absorb_peak", 5.0, 1.5, 0.2, cap=0.75) == (pytest.approx(0.55), 0.0)   # only above the cap
    assert flows("absorb_peak", 5.0, 0.8, 0.2, cap=0.75) == (0.0, 0.0)
    assert flows("charge_50", 5.0, 0.0, 0.0) == (0.5, 0.0) and flows("discharge_25", 5.0, 0.0, 0.0) == (0.0, 0.25)
    full_c, _ = flows("charge_100", 9.9, 0.0, 0.0)
    stored = (full_c - float(M.inverter_loss(full_c, b))) * b.efficiency
    assert stored == pytest.approx(0.1, abs=1e-9)                               # exactly fills the battery
    assert flows("discharge_100", 1.0, 0.0, 0.0) == (0.0, 0.0)                  # at the minimum charge
    c, d = M.battery_flows(np.arange(len(M.ACTIONS))[:, None], np.array([2.0, 6.0])[None, :], 1.0, 0.2, b)
    assert c.shape == d.shape == (len(M.ACTIONS), 2)                            # vectorised for the dynamic programs


def test_transition_balance_and_trips():
    b = M.Battery()
    new, cost, f = M.transition(b, 5.0, 0.5, 0.0, pv=1.0, load=0.2, buy=1.0, sell=0.3, cap=np.inf)
    loss = float(M.inverter_loss(0.5, b))
    assert float(new) == pytest.approx(5.0 + (0.5 - loss) * b.efficiency)
    assert float(f["export"]) == pytest.approx(0.3) and not f["trip"]
    assert float(cost) == pytest.approx(-0.3 * 0.3 + float(f["wear"]))
    new, cost, f = M.transition(b, 5.0, 0.0, 0.0, pv=1.5, load=0.2, buy=1.0, sell=0.05, cap=0.75)
    assert f["trip"] and float(f["import"]) == 0.2 and float(f["lost_pv"]) == 1.5 and float(new) == 5.0
    assert float(cost) == pytest.approx(0.2)                                    # the house runs on the grid


def test_wear_grows_with_power_and_full_charge():
    b = M.Battery()
    wear = lambda c, d, soc=5.0: float(M.transition(b, soc, c, d, 0, 0, 0, 0, np.inf)[2]["wear"])  # noqa: E731
    fast, slow = wear(0, 1.25), wear(0, 0.625)
    assert fast / 1.25 > slow / 0.625                                           # per kWh, fast discharge wears more
    assert wear(0, 0, soc=9.9) > wear(0, 0, soc=8.0) == 0.0                     # sitting full ages the battery


def test_tariffs_and_the_deposit():
    rce = np.array([0.5, -0.2])
    g11, dyn = M.Tariff(), M.Tariff(kind="dynamic")
    assert list(g11.buy(rce)) == pytest.approx([1.0, 1.0]) and list(g11.sell(rce)) == pytest.approx([0.5, 0.0])
    assert dyn.buy(rce)[0] == pytest.approx((0.5 + 0.05) * 1.23 + 0.38)
    surplus = dataclasses.replace(g11, theta=1.0)
    assert surplus.buy(rce)[0] == pytest.approx(0.38) and surplus.sell(rce)[0] == pytest.approx(0.5 * 0.3)
    # deposit 100 zł against 40 zł of energy: 40 used, 30 refunded (cap), 30 lost
    b = M.bill(g11, np.array([1.0]), grid_import=np.array([40 / 0.62]), grid_export=np.array([100.0]))
    assert (b["deposit_used_zl"], b["refund_zl"], b["lost_deposit_zl"]) == pytest.approx((40, 30, 30))
    assert b["total_zl"] == pytest.approx(40 / 0.62 * 0.38 + 40 - 40 - 30)
    with pytest.raises(ValueError):
        M.Tariff(kind="night").buy(rce)


def test_what_the_controller_knows():
    sc = make_scenario()
    morning = sc.start + 10 * 4                      # 10:00 on day 2: tomorrow not published yet
    afternoon = sc.start + 15 * 4                    # 15:00: tomorrow's prices are out
    spike = sc.start + 96 + 20                       # a spike tomorrow at 05:00
    sc.rce_raw[spike] = 9.0
    assert sc.price_forecast(afternoon)[spike - afternoon] == 9.0
    assert sc.price_forecast(morning)[spike - morning] != 9.0                    # unknown: yesterday's price
    assert sc.known_steps(morning) == 14 * 4 and sc.known_steps(afternoon) == 9 * 4 + 96
    f = sc.pv_forecast(sc.start + 40)
    assert f[0] == pytest.approx(sc.pv[sc.start + 40], rel=0.15)               # the next quarter is nearly right
    perfect = make_scenario(error=0.0)
    assert np.allclose(perfect.pv_forecast(perfect.start + 40), perfect.pv[perfect.start + 40:perfect.start + 40 + M.HORIZON])
    obs = M.observe(sc, sc.start + 40, 5.0)
    assert obs.shape == (M.OBS_SIZE,) and obs.dtype == np.float32 and obs[0] == pytest.approx(0.5)
    assert np.isfinite(obs).all()


def test_forecast_uncertainty_is_highest_on_partly_cloudy_days():
    days = np.repeat(np.arange(30), 96)
    pv = np.tile(np.r_[np.zeros(40), np.ones(16), np.zeros(40)], 30).astype(float)
    pv[days == 10] *= 0.5                                                       # partly cloudy
    pv[days == 20] *= 0.05                                                      # overcast
    sigma = M.forecast_uncertainty(pv, days)
    clear, partly, overcast = sigma[days == 5][0], sigma[days == 10][0], sigma[days == 20][0]
    assert clear < overcast < partly and clear == pytest.approx(0.08)


# ------------------------------------------------------------------ strategies
@pytest.mark.parametrize("kind", ["g11", "dynamic"])
def test_strategies_rank_as_expected(kind):
    sc = make_scenario(M.Tariff(kind=kind))
    none, rule, lp, dp, best = S.no_battery(sc), S.rule(sc), S.lp_mpc(sc), S.dp_mpc(sc), S.optimum(sc)
    for r in (rule, lp, dp):
        assert best.cost <= r.cost + 0.05                                       # the optimum is exact (up to its grid)
    assert rule.cost < none.cost and lp.cost < none.cost and dp.cost < none.cost
    b = sc.battery
    assert best.soc.min() >= b.soc_min * b.capacity_kwh - 1e-6 and best.soc.max() <= b.capacity_kwh + 1e-6


def test_a_weak_grid_trips_the_usual_inverter_but_not_the_optimum():
    sc = make_scenario(grid=WEAK)
    none, rule, best = S.no_battery(sc), S.rule(sc), S.optimum(sc)
    assert none.trips.sum() > 0 and rule.trips.sum() > 0                        # the battery fills, then noon trips
    assert best.trips.sum() == 0 and best.cost < rule.cost
    assert rule.settle(sc)["lost_pv_kwh"] > 0 and best.settle(sc)["lost_pv_kwh"] == 0


def test_optimum_shifts_energy_to_the_evening_with_a_dynamic_tariff():
    sc = make_scenario(M.Tariff(kind="dynamic"), M.Battery(10, 5, wear_zl_per_kwh=0.0, high_soc_zl_per_h=0.0))
    best = S.optimum(sc)
    hour = np.asarray(sc.index[sc.start:sc.end].tz_convert(D.TZ).hour)
    evening, noon = (hour >= 19) & (hour < 21), (hour >= 12) & (hour < 14)
    assert best.discharge[evening].sum() > best.discharge[noon].sum()
    assert best.charge[noon].sum() > best.charge[evening].sum()


def test_calibrate_picks_the_cheapest_theta():
    sc, bills = S.calibrate(make_scenario(), (0.0, 1.0))
    assert sc.tariff.theta == min(bills, key=bills.get) and set(bills) == {0.0, 1.0}


def test_to_action():
    b = M.Battery(10, 4)
    a = M.ACTIONS
    assert a[S.to_action(0, 0, 1.0, np.inf, b)] == "idle"
    assert a[S.to_action(0, 0, 1.0, 0.5, b)] == "absorb_peak"                  # a trip threatens: keep under the cap
    assert a[S.to_action(0.5, 0, 1.0, np.inf, b)] == "charge_surplus"
    assert a[S.to_action(0.25, 0, 1.0, 0.75, b)] == "absorb_peak"
    assert a[S.to_action(1.0, 0, 0.0, np.inf, b)] == "charge_100"
    assert a[S.to_action(0, 0.3, -0.3, np.inf, b)] == "cover_deficit"
    assert a[S.to_action(0, 0.5, 0.0, np.inf, b)] == "discharge_50"


# ------------------------------------------------------------------ the agent
def test_shipped_policy_runs_without_torch():
    policy = Policy(POLICY_PATH)
    sc = make_scenario(grid=WEAK)
    action = policy.act(M.observe(sc, sc.start + 50, 5.0))
    assert 0 <= action < len(M.ACTIONS)
    assert policy.meta["obs_size"] == M.OBS_SIZE and tuple(policy.meta["actions"]) == M.ACTIONS
    result = S.agent(sc, policy)
    assert result.cost < S.rule(sc).cost < S.no_battery(sc).cost               # beats the usual inverter here


def test_policy_rejects_another_observation(tmp_path):
    with np.load(POLICY_PATH) as f:
        arrays = dict(f)
    arrays["obs_mean"] = arrays["obs_mean"][:-1]
    np.savez(tmp_path / "old.npz", **arrays)
    with pytest.raises(ValueError):
        Policy(tmp_path / "old.npz")


def test_environment():
    gym = pytest.importorskip("gymnasium")
    from solary.battery.env import BatteryEnv, Place
    env = BatteryEnv([Place.build(synthetic_inputs(12))], days=2, seed=0)
    obs, _ = env.reset(seed=1)
    assert env.observation_space.contains(obs) and isinstance(env.action_space, gym.spaces.Discrete)
    total, done, steps = 0.0, False, 0
    while not done:
        obs, reward, done, truncated, _ = env.step(0)
        total += reward
        steps += 1
    assert steps == 2 * 96 and np.isfinite(total)


# ------------------------------------------------------------------ plan and API
def test_daily_plan(monkeypatch, cfg):
    from solary.battery import plan as P
    inp = synthetic_inputs(6, pd.Timestamp.now(tz=D.TZ).date() - pd.Timedelta(days=1))
    inp.rce[-4 * 96:] = np.nan                                                 # only today is published
    monkeypatch.setattr(P, "live", lambda lat, lon, cfg, days: inp)
    out = P.daily_plan(50.26, 19.02, [(6.0, 35, 180)], 4000, M.Battery(), M.Tariff(), WEAK, 0.5, Policy(), cfg=cfg)
    steps = out["steps"]
    assert out["controller"] == "agent" and out["now"] == steps[0] and len(steps) > 96
    assert all(0.0 <= s["soc"] <= 1.0 for s in steps)
    assert any(s["rce_zl_kwh"] is None for s in steps) and steps[0]["rce_zl_kwh"] is not None
    assert out["rule_trips"] >= out["trips"]
    json.dumps(out)


def test_battery_api(monkeypatch):
    from fastapi.testclient import TestClient
    from solary import api
    from solary.battery import plan as P
    seen = {}

    def report(lat, lon, planes, annual_kwh, battery, tariff, grid, soc, kwh_year, plan):
        seen.update(planes=planes, battery=battery, tariff=tariff, grid=grid, soc=soc, kwh_year=kwh_year)
        return {"ok": True}

    monkeypatch.setattr(P, "battery_report", report)
    client = TestClient(api.app)
    r = client.get("/api/battery", params={"lat": 50.26, "lon": 19.02, "planes": "3.2:35:180,2.8:35:90",
                                           "battery_kwh": 15, "tariff": "dynamic", "kwh_year": 5000, "export_limit": 3})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert seen["planes"] == [(3.2, 35, 180), (2.8, 35, 90)] and seen["battery"].power_kw == 7.5
    assert seen["tariff"].kind == "dynamic" and seen["kwh_year"] == 5000 and seen["grid"].export_limit_kw == 3
    client.get("/api/battery", params={"lat": 50.26, "lon": 19.02})
    assert seen["grid"].export_limit_kw is None
    for bad in ({"lat": 50}, {"lat": 50, "lon": 19, "planes": "6:35"}, {"lat": 50, "lon": 19, "tariff": "night"},
                {"lat": 50, "lon": 19, "planes": "6:95:180"}, {"lat": 50, "lon": 19, "soc": 2},
                {"lat": 50, "lon": 19, "export_limit": 0}):
        assert client.get("/api/battery", params=bad).status_code in (400, 422), bad
