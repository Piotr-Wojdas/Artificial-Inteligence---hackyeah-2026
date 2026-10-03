import json
import os
import time

import pytest
import requests

from solary import solar_api as api

KEY = "TEST-KEY-123"


class _Response:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content
        self.ok = status < 400
        self.reason = "reason"

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


@pytest.fixture
def key(monkeypatch):
    for name in api.KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", KEY)


def test_missing_key(monkeypatch):
    for name in api.KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    assert api.has_api_key() is False
    with pytest.raises(api.MissingApiKey):
        api.api_key()


@pytest.mark.parametrize("name", api.KEY_NAMES)
def test_key_from_any_accepted_variable(monkeypatch, name):
    for n in api.KEY_NAMES:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv(name, "abc")
    assert api.api_key() == "abc" and api.has_api_key()


def test_building_insights_is_cached(cfg, key, bi, monkeypatch):
    calls = []

    def get(url, params, timeout):
        calls.append(params)
        return _Response(200, bi)

    monkeypatch.setattr(api.requests, "get", get)
    assert api.building_insights(52.0, 19.0, cfg) == bi
    assert api.building_insights(52.0, 19.0, cfg) == bi
    assert len(calls) == 1
    assert calls[0]["key"] == KEY and calls[0]["requiredQuality"] == "HIGH"
    assert (cfg.roof_dir / "building_52.00000_19.00000.json").is_file()


def test_medium_quality_is_tried_when_high_is_missing(cfg, key, bi, monkeypatch):
    qualities = []

    def get(url, params, timeout):
        qualities.append(params["requiredQuality"])
        if params["requiredQuality"] == "HIGH":
            return _Response(404, {"error": {"message": "not found"}})
        return _Response(200, bi)

    monkeypatch.setattr(api.requests, "get", get)
    assert api.building_insights(52.0, 19.0, cfg) == bi
    assert qualities == ["HIGH", "MEDIUM"]


def test_no_roof_data(cfg, key, monkeypatch):
    not_found = _Response(404, {"error": {"message": "Requested entity was not found."}})
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: not_found)
    with pytest.raises(api.RoofNotFound):
        api.building_insights(52.0, 19.0, cfg)
    assert not cfg.roof_dir.exists() or not list(cfg.roof_dir.iterdir())   # nothing cached


def test_other_errors_carry_googles_message_but_never_the_key(cfg, key, monkeypatch):
    denied = _Response(403, {"error": {"message": "Solar API has not been used in project"}})
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: denied)
    with pytest.raises(api.SolarApiError) as e:
        api.building_insights(52.0, 19.0, cfg)
    assert "403" in str(e.value) and "has not been used" in str(e.value) and KEY not in str(e.value)


def test_network_errors_do_not_leak_the_key(cfg, key, monkeypatch):
    def get(url, params, timeout):
        raise requests.ConnectionError(f"Max retries exceeded with url: /v1/x?key={params['key']}")

    monkeypatch.setattr(api.requests, "get", get)
    with pytest.raises(api.SolarApiError) as e:
        api.building_insights(52.0, 19.0, cfg)
    assert KEY not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__   # the original error is not chained


def test_data_layers_downloads_three_geotiffs_once(cfg, key, bi, monkeypatch):
    calls = []

    def get(url, params, timeout):
        calls.append(url)
        if url.endswith("dataLayers:get"):
            assert 20 <= params["radiusMeters"] <= 100 and params["requiredQuality"] == "HIGH"
            return _Response(200, {f"{n}Url": f"https://solar.googleapis.com/v1/geoTiff:get?id={n}"
                                   for n in ("rgb", "mask", "annualFlux", "dsm")})
        assert params["key"] == KEY
        return _Response(200, content=url.encode())

    monkeypatch.setattr(api.requests, "get", get)
    paths = api.data_layers(bi, cfg)
    assert set(paths) == {"rgb", "mask", "annualFlux"}
    assert paths["mask"].read_bytes().endswith(b"id=mask")
    assert len(calls) == 4
    api.data_layers(bi, cfg)
    assert len(calls) == 4                    # cached


def test_data_layers_error(cfg, key, bi, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Response(500, {"error": {"message": "boom"}}))
    with pytest.raises(api.SolarApiError):
        api.data_layers(bi, cfg)


def test_cache_older_than_30_days_is_refetched_and_purged(cfg, key, bi, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Response(200, bi))
    api.building_insights(52.0, 19.0, cfg)
    cached = cfg.roof_dir / "building_52.00000_19.00000.json"
    layers = cfg.roof_dir / "layers_52.00000_19.00000"
    layers.mkdir()
    (layers / "rgb.tif").write_bytes(b"x")
    image = cfg.roof_dir / "panels_52.00000_19.00000_all.png"
    image.write_bytes(b"x")
    fresh = cfg.roof_dir / "building_1.00000_1.00000.json"
    fresh.write_text("{}")
    other = cfg.data_dir / "geocode.json"
    other.write_text("{}")

    old = time.time() - 31 * 86400
    for p in (cached, layers, image, other):
        os.utime(p, (old, old))
    assert api._fresh(cached, cfg) is False and api._fresh(fresh, cfg) is True

    assert api.purge_expired(cfg) == 3
    assert not cached.exists() and not layers.exists() and not image.exists()
    assert fresh.exists()
    assert other.exists()                     # only Google data expires, not the geocoding cache


def test_purge_without_a_cache_folder(cfg):
    assert api.purge_expired(cfg) == 0


def test_cached_file_is_valid_json(cfg, key, bi, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Response(200, bi))
    api.building_insights(52.0, 19.0, cfg)
    assert json.loads((cfg.roof_dir / "building_52.00000_19.00000.json").read_text(encoding="utf-8")) == bi


def test_data_layers_fetches_only_the_missing_layer(cfg, key, bi, monkeypatch):
    calls = []

    def get(url, params, timeout):
        calls.append(url)
        if url.endswith("dataLayers:get"):
            return _Response(200, {f"{n}Url": f"https://solar.googleapis.com/v1/geoTiff:get?id={n}"
                                   for n in ("rgb", "mask", "annualFlux", "dsm")})
        return _Response(200, content=url.encode())

    monkeypatch.setattr(api.requests, "get", get)
    api.data_layers(bi, cfg)
    assert len(calls) == 4
    paths = api.data_layers(bi, cfg, api.LAYOUT_LAYERS)          # the height map for our own layout
    assert set(paths) == {"mask", "annualFlux", "dsm"} and paths["dsm"].read_bytes().endswith(b"id=dsm")
    assert calls[4:] == ["https://solar.googleapis.com/v1/dataLayers:get",
                         "https://solar.googleapis.com/v1/geoTiff:get?id=dsm"]


def test_data_layers_without_the_requested_layer(cfg, key, bi, monkeypatch):
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Response(200, {"rgbUrl": "x", "maskUrl": "y"}))
    with pytest.raises(api.SolarApiError, match="dsm"):
        api.data_layers(bi, cfg, ("dsm",))
