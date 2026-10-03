import dataclasses

import pytest

from solary import production as prod


@pytest.mark.parametrize("azimuth, aspect", [(180, 0), (90, -90), (270, 90), (0, -180), (135, -45), (225, 45),
                                             (360, -180), (359, 179)])
def test_compass_azimuth_to_pvgis_aspect(azimuth, aspect):
    assert prod.pvgis_aspect(azimuth) == aspect


def test_orientation_is_whole_degrees_inside_pvgis_limits():
    assert prod._orientation(30.4, 180.2) == (30, 0)
    assert prod._orientation(95.0, 90.0) == (90, -90)
    assert prod._orientation(-1.0, 359.7) == (0, 180)


def test_fallback_profile_is_a_distribution():
    assert len(prod.FALLBACK_MONTHLY_SHARE_PL) == 12
    assert sum(prod.FALLBACK_MONTHLY_SHARE_PL) == pytest.approx(1.0, abs=0.001)
    assert max(prod.FALLBACK_MONTHLY_SHARE_PL) == prod.FALLBACK_MONTHLY_SHARE_PL[5]   # June
    assert min(prod.FALLBACK_MONTHLY_SHARE_PL) == prod.FALLBACK_MONTHLY_SHARE_PL[11]  # December


def test_roof_estimate_one_segment(bi, cfg, fake_pvgis):
    res = prod.estimate_roof(bi, 2, cfg)       # the first two panels: both on the south segment
    assert res["panels"] == 2 and res["kwp"] == pytest.approx(0.8)
    assert res["kwh_year"] == pytest.approx((450 + 440) * 0.86)   # Google DC x (1 - 14 % losses)
    assert res["kwh_per_kwp"] == pytest.approx((450 + 440) * 0.86 / 0.8)
    assert sum(res["monthly_kwh"]) == pytest.approx(res["kwh_year"])
    assert res["monthly_source"] == "pvgis"
    assert res["pvgis_kwh_year"] == pytest.approx(1000 * 0.8)
    (seg,) = res["per_segment"]
    assert seg["segment"] == 0 and seg["facing"] == "S" and seg["panels"] == 2
    assert seg["pvgis_kwh_per_kwp"] == 1000
    assert seg["vs_pvgis"] == pytest.approx((450 + 440) * 0.86 / 0.8 / 1000)
    assert fake_pvgis == [(30, 0)]             # one PVGIS call: tilt 30, facing south


def test_roof_estimate_two_segments(bi, cfg, fake_pvgis):
    res = prod.estimate_roof(bi, 6, cfg)
    south, east = res["per_segment"]
    assert (south["panels"], east["panels"]) == (3, 3)
    assert south["kwh_year"] == pytest.approx((450 + 440 + 445) * 0.86)
    assert east["kwh_year"] == pytest.approx((380 + 370 + 360) * 0.86)
    assert res["kwh_year"] == pytest.approx(south["kwh_year"] + east["kwh_year"])
    assert res["kwp"] == pytest.approx(2.4)
    assert sum(res["monthly_kwh"]) == pytest.approx(res["kwh_year"])
    assert res["pvgis_kwh_year"] == pytest.approx(1000 * 1.2 + 800 * 1.2)
    assert sorted(fake_pvgis) == [(20, -90), (30, 0)]


def test_months_follow_each_segments_own_profile(bi, cfg, monkeypatch):
    def fetch(lat, lon, tilt, aspect, cfg):
        monthly = [0.0] * 12
        monthly[5 if aspect == 0 else 0] = 900.0      # south: everything in June, east: everything in January
        return {"kwh_per_kwp_year": 900.0, "monthly_kwh_per_kwp": monthly}

    monkeypatch.setattr(prod, "_fetch", fetch)
    res = prod.estimate_roof(bi, 6, cfg)
    south, east = res["per_segment"]
    assert res["monthly_kwh"][5] == pytest.approx(south["kwh_year"])
    assert res["monthly_kwh"][0] == pytest.approx(east["kwh_year"])
    assert sum(res["monthly_kwh"]) == pytest.approx(res["kwh_year"])


def test_roof_estimate_without_pvgis_uses_the_polish_profile(bi, cfg, no_pvgis):
    res = prod.estimate_roof(bi, 3, cfg)
    assert res["monthly_source"] == "typical_poland"
    assert res["pvgis_kwh_year"] is None
    assert res["kwh_year"] == pytest.approx((450 + 440 + 380) * 0.86)   # the yearly figure does not need PVGIS
    assert sum(res["monthly_kwh"]) == pytest.approx(res["kwh_year"])
    assert res["monthly_kwh"][5] > res["monthly_kwh"][11] * 4          # June far above December
    assert all(r["pvgis_kwh_per_kwp"] is None and r["vs_pvgis"] is None for r in res["per_segment"])


def test_other_panel_wattage_scales_power_and_energy(bi, cfg, fake_pvgis):
    res = prod.estimate_roof(bi, 2, dataclasses.replace(cfg, panel_watts=450.0))
    assert res["kwp"] == pytest.approx(0.9)
    assert res["kwh_year"] == pytest.approx((450 + 440) * 0.86 * 450 / 400)
    assert res["kwh_per_kwp"] == pytest.approx((450 + 440) * 0.86 / 0.8)   # per kWp it stays the same


def test_other_system_loss(bi, cfg, fake_pvgis):
    res = prod.estimate_roof(bi, 1, dataclasses.replace(cfg, system_loss_pct=20.0))
    assert res["kwh_year"] == pytest.approx(450 * 0.80)


def test_yield_order_picks_the_best_panels(bi, cfg, fake_pvgis):
    res = prod.estimate_roof(bi, 3, dataclasses.replace(cfg, panel_order="yield"))
    assert res["kwh_year"] == pytest.approx((450 + 445 + 440) * 0.86)


def test_sizes_table(bi, cfg):
    small = dataclasses.replace(cfg, sizes_kwp=(0.8, 1.2, 5.0))
    rows = prod.sizes_table(bi, small)
    assert [r["panels"] for r in rows] == [2, 3, 6]             # 5 kWp does not fit, the full roof (6) is added
    assert rows[0]["kwh_year"] == pytest.approx((450 + 440) * 0.86)
    assert rows[2]["kwp"] == pytest.approx(2.4)
    assert rows[2]["kwh_year"] == pytest.approx((450 + 440 + 380 + 445 + 370 + 360) * 0.86)
    assert rows[2]["kwh_per_kwp"] == pytest.approx(rows[2]["kwh_year"] / 2.4)
    for r in rows:                                               # the table agrees with the full estimate
        assert r["kwh_year"] == pytest.approx(prod.estimate_roof(bi, r["panels"], small)["kwh_year"])


def test_sizes_table_empty_roof(bi, cfg):
    bi["solarPotential"]["solarPanels"] = []
    assert prod.sizes_table(bi, cfg) == []


def test_generic_estimate(cfg, fake_pvgis):
    res = prod.estimate_generic(52.0, 19.0, 5.0, 35.0, 180.0, cfg)
    assert res["kwh_year"] == pytest.approx(5000) and res["kwh_per_kwp"] == 1000
    assert sum(res["monthly_kwh"]) == pytest.approx(5000)
    assert res["monthly_source"] == "pvgis" and res["facing"] == "S"
    assert fake_pvgis == [(35, 0)]
    east = prod.estimate_generic(52.0, 19.0, 5.0, 35.0, 90.0, cfg)
    assert east["kwh_year"] == pytest.approx(4000) and east["facing"] == "E"


def test_generic_estimate_without_pvgis(cfg, no_pvgis):
    res = prod.estimate_generic(52.0, 19.0, 4.0, 35.0, 180.0, cfg)
    assert res["monthly_source"] == "typical_poland"
    assert res["kwh_year"] == pytest.approx(4 * prod.FALLBACK_KWH_PER_KWP_PL)
    assert sum(res["monthly_kwh"]) == pytest.approx(res["kwh_year"])


def test_pvgis_results_are_cached_on_disk(cfg, fake_pvgis):
    first = prod.pvgis_lookup(52.0, 19.0, {(30, 0), (20, -90)}, cfg)
    again = prod.pvgis_lookup(52.001, 19.001, {(30, 0)}, cfg)       # same 0.01 deg cell -> served from the cache
    assert len(fake_pvgis) == 2
    assert again[(30, 0)] == first[(30, 0)]
    prod.pvgis_lookup(52.5, 19.0, {(30, 0)}, cfg)                    # another location -> a new request
    assert len(fake_pvgis) == 3
    assert (cfg.data_dir / "pvgis_cache.json").is_file()


def test_failed_pvgis_calls_are_not_cached(cfg, monkeypatch):
    answers = iter([None, {"kwh_per_kwp_year": 950.0, "monthly_kwh_per_kwp": [950 / 12] * 12}])
    monkeypatch.setattr(prod, "_fetch", lambda *a, **k: next(answers))
    assert prod.pvgis_lookup(52.0, 19.0, {(30, 0)}, cfg)[(30, 0)] is None
    assert prod.pvgis_lookup(52.0, 19.0, {(30, 0)}, cfg)[(30, 0)]["kwh_per_kwp_year"] == 950.0


class _Response:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


def test_fetch_parses_a_pvgis_response(cfg, monkeypatch):
    seen = {}

    def get(url, params, timeout):
        seen.update(params)
        monthly = [{"month": m, "E_m": float(m)} for m in range(12, 0, -1)]   # out of order on purpose
        return _Response({"outputs": {"monthly": {"fixed": monthly}, "totals": {"fixed": {"E_y": 78.0}}}})

    monkeypatch.setattr(prod.requests, "get", get)
    res = prod._fetch(52.123, 19.456, 30, -90, cfg)
    assert res == {"kwh_per_kwp_year": 78.0, "monthly_kwh_per_kwp": [float(m) for m in range(1, 13)]}
    assert seen["lat"] == 52.12 and seen["lon"] == 19.46 and seen["angle"] == 30 and seen["aspect"] == -90
    assert seen["peakpower"] == 1 and seen["loss"] == 14.0 and seen["mountingplace"] == "building"


@pytest.mark.parametrize("payload, status", [
    ({"message": "bad"}, 400), ({}, 200), ({"outputs": {}}, 200),
    ({"outputs": {"monthly": {"fixed": []}, "totals": {"fixed": {"E_y": 1}}}}, 200)])
def test_fetch_returns_none_on_any_bad_answer(cfg, monkeypatch, payload, status):
    monkeypatch.setattr(prod.requests, "get", lambda *a, **k: _Response(payload, status))
    assert prod._fetch(52.0, 19.0, 30, 0, cfg) is None


def test_fetch_returns_none_when_offline(cfg, monkeypatch):
    import requests

    def get(*a, **k):
        raise requests.ConnectionError("no network")

    monkeypatch.setattr(prod.requests, "get", get)
    assert prod._fetch(52.0, 19.0, 30, 0, cfg) is None
