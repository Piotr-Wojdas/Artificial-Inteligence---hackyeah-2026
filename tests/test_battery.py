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


def make_scenario(tariff=M.Tariff(), battery=M.Battery(), kwp=6.0, days=6, error=1.0) -> M.Scenario:
    inp = synthetic_inputs(days)
    load, expected = D.load_profile(inp.index, 4000, seed=3)
    return M.Scenario(index=inp.index, rce=inp.rce, pv=inp.pv([(kwp, 35, 180)]), load=load, load_expected=expected,
                      battery=battery, tariff=tariff, seed=5, pv_error_scale=error)


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
def test_battery_flows_and_limits():
    b = M.Battery(capacity_kwh=10, power_kw=4)
    a = {name: i for i, name in enumerate(M.ACTIONS)}
    assert M.battery_flows(a["self_consumption"], 5.0, pv=2.0, load=0.5, b=b) == (1.0, 0.0)   # 4 kW x 15 min
    assert M.battery_flows(a["self_consumption"], 5.0, pv=0.0, load=0.3, b=b) == (0.0, pytest.approx(0.3))
    assert M.battery_flows(a["charge_surplus"], 5.0, pv=0.0, load=0.3, b=b) == (0.0, 0.0)
    assert M.battery_flows(a["cover_deficit"], 5.0, pv=2.0, load=0.5, b=b) == (0.0, 0.0)      # sells the surplus
    assert M.battery_flows(a["charge_full"], 9.9, pv=0.0, load=0.0, b=b)[0] == pytest.approx(0.1 / 0.95)
    assert M.battery_flows(a["discharge_full"], 1.1, pv=0.0, load=0.0, b=b)[1] == pytest.approx(0.1 * 0.95)


def test_apply_keeps_the_energy_balance():
    sc = make_scenario()
    t = sc.start + 12 * 4
    soc, cost, f = M.apply(sc, t, 5.0, charge=0.5, discharge=0.0)
    assert soc == pytest.approx(5.0 + 0.5 * 0.95)
    assert f["import"] - f["export"] == pytest.approx(sc.load[t] - sc.pv[t] + 0.5)
    assert cost == pytest.approx(f["import"] * sc.buy[t] - f["export"] * sc.sell[t])


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
    sc = make_scenario(days=4)
    local = sc.index.tz_convert(D.TZ)
    morning = sc.start + 10 * 4                      # 10:00 on day 2: tomorrow not published yet
    afternoon = sc.start + 15 * 4                    # 15:00: tomorrow's prices are out
    sc.rce_raw[sc.start + 96 + 20] = 9.0              # a spike tomorrow at 05:00
    assert sc.price_forecast(afternoon)[sc.start + 96 + 20 - afternoon] == 9.0
    assert sc.price_forecast(morning)[sc.start + 96 + 20 - morning] != 9.0      # unknown: yesterday's price
    assert local[morning].hour == 10
    f = sc.pv_forecast(sc.start + 40)
    assert f[0] == pytest.approx(sc.pv[sc.start + 40], rel=0.1)                # the next quarter is nearly right
    perfect = make_scenario(days=4, error=0.0)
    assert np.allclose(perfect.pv_forecast(perfect.start + 40), perfect.pv[perfect.start + 40:perfect.start + 136])
    obs = M.observe(sc, sc.start + 40, 5.0)
    assert obs.shape == (M.OBS_SIZE,) and obs.dtype == np.float32 and obs[0] == pytest.approx(0.5)


# ------------------------------------------------------------------ strategies
@pytest.mark.parametrize("kind", ["g11", "dynamic"])
def test_strategies_rank_as_expected(kind):
    sc = make_scenario(M.Tariff(kind=kind))
    none, rule, mpc, best = S.no_battery(sc), S.rule(sc), S.mpc(sc), S.optimum(sc)
    assert best.cost <= mpc.cost + 1e-6 and best.cost <= rule.cost + 1e-6 and rule.cost < none.cost
    assert mpc.cost < none.cost
    b = sc.battery
    assert best.soc.min() >= b.soc_min * b.capacity_kwh - 1e-6 and best.soc.max() <= b.capacity_kwh + 1e-6
    assert best.charge.max() <= b.power_kw * D.STEP_H + 1e-6
    assert rule.settle(sc)["total_zl"] < none.settle(sc)["total_zl"]


def test_optimum_sells_in_the_evening_peak_with_a_dynamic_tariff():
    sc = make_scenario(M.Tariff(kind="dynamic"), M.Battery(10, 5, wear_zl_per_kwh=0.0))
    best = S.optimum(sc)
    local = sc.index[sc.start:sc.end].tz_convert(D.TZ)
    evening = (np.asarray(local.hour) >= 19) & (np.asarray(local.hour) < 21)
    noon = (np.asarray(local.hour) >= 12) & (np.asarray(local.hour) < 14)
    assert best.discharge[evening].sum() > best.discharge[noon].sum()
    assert best.charge[noon].sum() > best.charge[evening].sum()


def test_calibrate_picks_the_cheapest_theta():
    sc, bills = S.calibrate(make_scenario(), (0.0, 1.0))
    assert sc.tariff.theta == min(bills, key=bills.get) and set(bills) == {0.0, 1.0}


def test_to_action():
    b = M.Battery(10, 4)
    a = M.ACTIONS
    assert a[S.to_action(0, 0, 1.0, b)] == "idle"
    assert a[S.to_action(0.5, 0, 1.0, b)] == "charge_surplus"
    assert a[S.to_action(1.0, 0, 0.0, b)] == "charge_full"
    assert a[S.to_action(0, 0.3, -0.3, b)] == "cover_deficit"
    assert a[S.to_action(0, 0.5, 0.0, b)] == "discharge_half"


# ------------------------------------------------------------------ the agent
def test_shipped_policy_runs_without_torch():
    policy = Policy(POLICY_PATH)
    sc = make_scenario()
    action = policy.act(M.observe(sc, sc.start + 50, 5.0))
    assert 0 <= action < len(M.ACTIONS)
    assert policy.meta["obs_size"] == M.OBS_SIZE and tuple(policy.meta["actions"]) == M.ACTIONS
    result = S.agent(sc, policy)
    assert result.cost < S.no_battery(sc).cost                                 # it saves money on clear days


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
    assert steps == 2 * 96 and total > 0                                       # self-consumption beats no battery


# ------------------------------------------------------------------ plan and API
def test_daily_plan(monkeypatch, cfg):
    from solary.battery import plan as P
    inp = synthetic_inputs(5, pd.Timestamp.now(tz=D.TZ).date() - pd.Timedelta(days=1))
    inp.rce[-3 * 96:] = np.nan                                                 # only today is published
    monkeypatch.setattr(P, "live", lambda lat, lon, cfg, days: inp)
    out = P.daily_plan(50.26, 19.02, [(6.0, 35, 180)], 4000, M.Battery(), M.Tariff(), 0.5, Policy(), cfg=cfg)
    steps = out["steps"]
    assert out["controller"] == "agent" and out["now"] == steps[0] and len(steps) > 96
    assert all(0.0 <= s["soc"] <= 1.0 for s in steps)
    assert any(s["rce_zl_kwh"] is None for s in steps) and steps[0]["rce_zl_kwh"] is not None
    json.dumps(out)


def test_battery_api(monkeypatch):
    from fastapi.testclient import TestClient
    from solary import api
    from solary.battery import plan as P
    seen = {}

    def report(lat, lon, planes, annual_kwh, battery, tariff, soc, kwh_year, plan):
        seen.update(planes=planes, battery=battery, tariff=tariff, soc=soc, kwh_year=kwh_year)
        return {"ok": True}

    monkeypatch.setattr(P, "battery_report", report)
    client = TestClient(api.app)
    r = client.get("/api/battery", params={"lat": 50.26, "lon": 19.02, "planes": "3.2:35:180,2.8:35:90",
                                           "battery_kwh": 15, "tariff": "dynamic", "kwh_year": 5000})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert seen["planes"] == [(3.2, 35, 180), (2.8, 35, 90)] and seen["battery"].power_kw == 7.5
    assert seen["tariff"].kind == "dynamic" and seen["kwh_year"] == 5000
    for bad in ({"lat": 50}, {"lat": 50, "lon": 19, "planes": "6:35"}, {"lat": 50, "lon": 19, "tariff": "night"},
                {"lat": 50, "lon": 19, "planes": "6:95:180"}, {"lat": 50, "lon": 19, "soc": 2}):
        assert client.get("/api/battery", params=bad).status_code in (400, 422), bad
