import pytest
import requests

from solary import geocode as geo


class _Response:
    def __init__(self, hits):
        self._hits = hits

    def raise_for_status(self):
        pass

    def json(self):
        return self._hits


def hit(lat, lon, name, rank=30):
    return {"lat": str(lat), "lon": str(lon), "display_name": name, "place_rank": rank}


@pytest.fixture
def nominatim(monkeypatch):
    """Stand-in for the Nominatim server: `answer["hits"]` is returned, every request is recorded."""
    calls, answer = [], {"hits": []}

    def get(url, params, timeout, headers):
        calls.append({"params": params, "headers": headers})
        return _Response(answer["hits"])

    monkeypatch.setattr(geo.requests, "get", get)
    monkeypatch.setattr(geo, "MIN_INTERVAL_S", 0.0)
    return calls, answer


@pytest.mark.parametrize("raw, clean", [
    ("ul. Mariacka 1, Katowice", "Mariacka 1, Katowice"),
    ("ul.Mariacka 1, Katowice", "Mariacka 1, Katowice"),
    ("UL. Mariacka 1,  Katowice ", "Mariacka 1, Katowice"),
    ("ulica Długa 5, Kraków", "Długa 5, Kraków"),
    ("Katowice, ul. Mariacka 1", "Katowice, Mariacka 1"),
    ("ul Mariacka 1", "Mariacka 1"),
    ("al. Jerozolimskie 100, Warszawa", "al. Jerozolimskie 100, Warszawa"),   # only "ul." gets in the way
    ("Ulanów, Rynek 1", "Ulanów, Rynek 1"),
    ("Paul Street 3", "Paul Street 3"),
    ("  ,  ", ""),
])
def test_normalise(raw, clean):
    assert geo.normalise(raw) == clean


def test_geocode_returns_the_best_match(cfg, nominatim):
    calls, answer = nominatim
    answer["hits"] = [hit(50.2574, 19.02476, "1, Mariacka, Katowice")]
    res = geo.geocode("ul. Mariacka 1, Katowice", cfg)
    assert res == {"lat": 50.2574, "lon": 19.02476, "label": "1, Mariacka, Katowice", "house_level": True,
                   "alternatives": []}
    sent = calls[0]
    assert sent["params"]["q"] == "Mariacka 1, Katowice"
    assert sent["params"]["countrycodes"] == "pl" and sent["params"]["format"] == "jsonv2"
    assert sent["headers"]["User-Agent"].startswith("solary") and sent["headers"]["Accept-Language"] == "pl"


def test_street_only_match_is_flagged(cfg, nominatim):
    _, answer = nominatim
    answer["hits"] = [hit(50.2574, 19.0271, "Mariacka, Katowice", rank=26)]
    assert geo.geocode("Mariacka, Katowice", cfg)["house_level"] is False


def test_alternatives_skip_duplicates_of_the_same_building(cfg, nominatim):
    _, answer = nominatim
    answer["hits"] = [
        hit(49.8152, 22.2329, "1, Rynek, Zamość, Dynów"),
        hit(50.7179, 23.2534, "1, Rynek Solny, Zamość"),
        hit(50.71791, 23.25335, "Restauracja, 1, Rynek Solny, Zamość"),   # ~8 m from the previous one
    ]
    res = geo.geocode("Rynek 1, Zamość", cfg)
    assert res["label"] == "1, Rynek, Zamość, Dynów"
    assert [a["label"] for a in res["alternatives"]] == ["1, Rynek Solny, Zamość"]
    assert "alternatives" not in res["alternatives"][0]


def test_results_are_cached(cfg, nominatim):
    calls, answer = nominatim
    answer["hits"] = [hit(52.0, 19.0, "somewhere")]
    first = geo.geocode("Testowa 1, Łódź", cfg)
    assert geo.geocode("ul. testowa 1,   łódź", cfg) == first   # same address after normalising
    assert len(calls) == 1
    assert (cfg.data_dir / "geocode.json").is_file()


def test_not_found(cfg, nominatim):
    with pytest.raises(geo.AddressNotFound):
        geo.geocode("Nieistniejąca 999, Nigdzie", cfg)
    with pytest.raises(geo.AddressNotFound):
        geo.geocode("   ", cfg)


def test_network_failure_is_reported_without_details(cfg, monkeypatch):
    def get(*a, **k):
        raise requests.ConnectionError("https://nominatim.openstreetmap.org/search?q=secret")

    monkeypatch.setattr(geo.requests, "get", get)
    monkeypatch.setattr(geo, "MIN_INTERVAL_S", 0.0)
    with pytest.raises(geo.GeocodingError) as e:
        geo.geocode("Mariacka 1, Katowice", cfg)
    assert "secret" not in str(e.value)


def test_world_wide_search_when_no_country_is_set(cfg, nominatim):
    import dataclasses

    calls, answer = nominatim
    answer["hits"] = [hit(48.2, 16.37, "Wien")]
    geo.geocode("Stephansplatz 1, Wien", dataclasses.replace(cfg, country_codes=None))
    assert "countrycodes" not in calls[0]["params"]
