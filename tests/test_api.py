import dataclasses

import pytest
from fastapi.testclient import TestClient

from solary import api
from solary.geocode import AddressNotFound, GeocodingError
from solary.solar_api import MissingApiKey, SolarApiError


@pytest.fixture
def client():
    return TestClient(api.app)


def test_health(client, monkeypatch):
    monkeypatch.setattr(api, "has_api_key", lambda: True)
    assert client.get("/api/health").json() == {"ok": True, "google_key": True}
    monkeypatch.setattr(api, "has_api_key", lambda: False)
    assert client.get("/api/health").json()["google_key"] is False


def test_roof_passes_the_parameters_and_turns_file_names_into_urls(client, monkeypatch):
    seen = {}

    def analyze(**kwargs):
        seen.update(kwargs)
        return {"roof_available": True, "images": {"selected": "panels_x_google_15.png"}}

    monkeypatch.setattr(api, "analyze", analyze)
    r = client.get("/api/roof", params={"address": "Mariacka 1, Katowice", "kwp": 6})
    assert r.status_code == 200
    assert r.json()["images"] == {"selected": "/api/roof/image/panels_x_google_15.png"}
    assert seen == {"address": "Mariacka 1, Katowice", "lat": None, "lon": None, "kwp": 6.0, "panels": None,
                    "images": True, "tilt": None, "azimuth": None}
    client.get("/api/roof", params={"lat": 52, "lon": 19, "panels": 15, "images": "false", "tilt": 30, "azimuth": 90})
    assert seen == {"address": None, "lat": 52.0, "lon": 19.0, "kwp": None, "panels": 15, "images": False,
                    "tilt": 30.0, "azimuth": 90.0}


def test_roof_with_our_layout(client, monkeypatch):
    seen = {}

    def analyze(**kwargs):
        seen.update(kwargs)
        return {"roof_available": True, "images": {}}

    monkeypatch.setattr(api, "analyze", analyze)
    assert client.get("/api/roof", params={"address": "Mariacka 1, Katowice", "layout": "own"}).status_code == 200
    assert seen["cfg"].layout == "own" and seen["cfg"].layout_margin_m == api.CONFIG.layout_margin_m
    client.get("/api/roof", params={"address": "Mariacka 1, Katowice", "layout": "own", "margin": 0.5})
    assert seen["cfg"].layout == "own" and seen["cfg"].layout_margin_m == 0.5
    assert seen["cfg"].data_dir == api.CONFIG.data_dir             # everything else as configured


@pytest.mark.parametrize("params", [{}, {"lat": 52}, {"address": "  "}, {"lat": 100, "lon": 19},
                                    {"lat": 52, "lon": 19, "kwp": 0}, {"lat": 52, "lon": 19, "panels": 0},
                                    {"lat": 52, "lon": 19, "tilt": 91}, {"address": "x" * 301},
                                    {"address": "x", "layout": "magic"}, {"address": "x", "margin": -1},
                                    {"address": "x", "margin": 3}])
def test_bad_requests(client, monkeypatch, params):
    monkeypatch.setattr(api, "analyze", lambda **k: pytest.fail("must not be called"))
    assert client.get("/api/roof", params=params).status_code in (400, 422)


@pytest.mark.parametrize("error, status", [(AddressNotFound("nope"), 404), (MissingApiKey("no key"), 503),
                                           (SolarApiError("google down"), 502), (GeocodingError("osm down"), 502)])
def test_errors_become_http_errors(client, monkeypatch, error, status):
    def analyze(**kwargs):
        raise error

    monkeypatch.setattr(api, "analyze", analyze)
    r = client.get("/api/roof", params={"address": "Mariacka 1, Katowice"})
    assert r.status_code == status and r.json()["detail"] == str(error)


def test_images_are_served_only_from_the_cache_folder(client, monkeypatch, tmp_path):
    cfg = dataclasses.replace(api.CONFIG, data_dir=tmp_path)
    monkeypatch.setattr(api, "CONFIG", cfg)
    cfg.roof_dir.mkdir(parents=True)
    (cfg.roof_dir / "panels_50.25736_19.02476_google_15.png").write_bytes(b"\x89PNG fake")
    (cfg.roof_dir / "building_50.25736_19.02476.json").write_text("{}")
    (tmp_path / "secret.png").write_bytes(b"secret")

    ok = client.get("/api/roof/image/panels_50.25736_19.02476_google_15.png")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png" and ok.content == b"\x89PNG fake"
    for name in ("panels_missing.png", "building_50.25736_19.02476.json", "..%2Fsecret.png", "%2E%2E%2Fsecret.png",
                 "secret.png", "panels_..%5C..%5Csecret.png"):
        assert client.get(f"/api/roof/image/{name}").status_code == 404, name


def test_demo_page(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Solary" in r.text and "/api/roof" in r.text
