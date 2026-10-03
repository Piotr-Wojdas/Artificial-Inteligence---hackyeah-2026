import json

import pytest

from solary import service
from solary.errors import SolaryError
from solary.solar_api import RoofNotFound, SolarApiError


@pytest.fixture
def roof(bi, monkeypatch, fake_pvgis):
    """Google answers with the fixture building; no map layers (images are tested in test_render)."""
    monkeypatch.setattr(service, "building_insights", lambda lat, lon, cfg: bi)
    monkeypatch.setattr(service, "_render_all", lambda bi, lat, lon, n, cfg: {
        "confirm": "confirm_x.png", "all": "panels_x_all.png", "selected": f"panels_x_google_{n}.png"})
    return bi


@pytest.fixture
def no_roof(monkeypatch, fake_pvgis):
    def missing(lat, lon, cfg):
        raise RoofNotFound("no data")

    monkeypatch.setattr(service, "building_insights", missing)


def test_roof_by_coordinates(roof, cfg):
    res = service.analyze(lat=52.0, lon=19.0, kwp=0.8, cfg=cfg)
    assert res["roof_available"] is True and res["warnings"] == []
    assert res["query"] == {"address": None, "lat": 52.0, "lon": 19.0}
    assert res["address_label"] is None and res["alternatives"] == []
    assert res["building"] == {"lat": 52.0, "lon": 19.0, "distance_m": 0.0, "roof_area_m2": 120.0, "max_panels": 6,
                               "max_kwp": pytest.approx(2.4), "imagery_quality": "HIGH", "imagery_date": "2023-06"}
    assert res["panel"] == {"watts": 400.0, "google_watts": 400.0, "height_m": 1.879, "width_m": 1.045}
    assert res["selected"]["panels"] == 2 and res["selected"]["kwh_year"] == pytest.approx(890 * 0.86)
    assert len(res["segments"]) == 2
    assert res["sizes"][-1]["panels"] == 6
    assert res["images"]["selected"] == "panels_x_google_2.png"
    assert res["assumptions"]["system_loss_pct"] == 14.0
    assert "Google" in res["attribution"] and "OpenStreetMap" in res["attribution"]
    json.dumps(res)                                    # everything is plain JSON


def test_default_size_and_panel_count(roof, cfg):
    assert service.analyze(lat=52.0, lon=19.0, cfg=cfg)["selected"]["panels"] == 6   # 6 kWp default, roof fits 2.4
    assert service.analyze(lat=52.0, lon=19.0, panels=4, cfg=cfg)["selected"]["panels"] == 4
    assert service.analyze(lat=52.0, lon=19.0, panels=99, cfg=cfg)["selected"]["panels"] == 6
    assert service.analyze(lat=52.0, lon=19.0, panels=3, kwp=0.4, cfg=cfg)["selected"]["panels"] == 3  # panels win


def test_roof_by_address(roof, cfg, monkeypatch):
    place = {"lat": 52.0, "lon": 19.0, "label": "1, Testowa, Łódź", "house_level": True,
             "alternatives": [{"lat": 51.0, "lon": 18.0, "label": "1, Testowa, Kalisz", "house_level": True}]}
    monkeypatch.setattr(service, "geocode", lambda address, cfg: place)
    res = service.analyze(address="Testowa 1, Łódź", cfg=cfg)
    assert res["address_label"] == "1, Testowa, Łódź" and res["query"]["address"] == "Testowa 1, Łódź"
    assert res["alternatives"] == place["alternatives"]
    assert res["warnings"] == []


def test_warnings(roof, cfg, monkeypatch, no_pvgis):
    street = {"lat": 52.0012, "lon": 19.0, "label": "Testowa, Łódź", "house_level": False, "alternatives": []}
    monkeypatch.setattr(service, "geocode", lambda address, cfg: street)

    def broken(*a):
        raise SolarApiError("layers down")

    monkeypatch.setattr(service, "_render_all", broken)
    res = service.analyze(address="Testowa, Łódź", cfg=cfg)
    assert res["warnings"] == ["street_only", "monthly_fallback", "far_from_address", "images_unavailable"]
    assert res["building"]["distance_m"] > 100 and res["images"] == {}
    assert res["selected"]["kwh_year"] > 0             # the numbers are still there


def test_images_can_be_skipped(roof, cfg):
    assert service.analyze(lat=52.0, lon=19.0, images=False, cfg=cfg)["images"] == {}


def test_generic_estimate_when_google_has_no_roof(no_roof, cfg):
    res = service.analyze(lat=50.717, lon=23.252, kwp=5, cfg=cfg)
    assert res["roof_available"] is False and res["reason"] == "no_roof_data"
    g = res["generic"]
    assert (g["kwp"], g["tilt_deg"], g["azimuth_deg"], g["facing"]) == (5, 35.0, 180.0, "S")
    assert g["kwh_year"] == pytest.approx(5000)
    assert [r["kwp"] for r in res["sizes"]] == list(cfg.sizes_kwp)
    assert res["images"] == {} and "Google" not in res["attribution"] and "PVGIS" in res["attribution"]
    assert "selected" not in res and "building" not in res
    json.dumps(res)


def test_generic_estimate_with_own_orientation(no_roof, cfg):
    res = service.analyze(lat=50.717, lon=23.252, kwp=8, tilt=20, azimuth=90, cfg=cfg)
    assert res["generic"]["facing"] == "E" and res["generic"]["kwh_year"] == pytest.approx(8 * 800)
    assert service.analyze(lat=50.717, lon=23.252, cfg=cfg)["generic"]["kwp"] == cfg.default_kwp


def test_generic_estimate_when_no_panel_fits(roof, cfg):
    roof["solarPotential"]["solarPanels"] = []
    res = service.analyze(lat=52.0, lon=19.0, cfg=cfg)
    assert res["roof_available"] is False and res["reason"] == "no_panels_fit"


@pytest.mark.parametrize("kwargs", [{}, {"lat": 52.0}, {"address": "   "}, {"lat": 95.0, "lon": 19.0},
                                    {"lat": 52.0, "lon": 190.0}, {"lat": 52.0, "lon": 19.0, "kwp": 0},
                                    {"lat": 52.0, "lon": 19.0, "kwp": -3}, {"lat": 52.0, "lon": 19.0, "panels": 0}])
def test_bad_input(roof, cfg, kwargs):
    with pytest.raises(SolaryError):
        service.analyze(cfg=cfg, **kwargs)


def test_bad_tilt_in_generic_mode(no_roof, cfg):
    with pytest.raises(SolaryError):
        service.analyze(lat=52.0, lon=19.0, tilt=120, cfg=cfg)
