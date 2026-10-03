"""Offline fixtures: a hand-made buildingInsights response and a config that writes to a temp folder.
No test talks to Google, PVGIS or OpenStreetMap."""

import dataclasses

import pytest

from solary.config import CONFIG

LAT, LON = 52.0, 19.0


def make_panel(segment: int, kwh: float, dlat: float = 0.0, dlon: float = 0.0, orientation: str = "LANDSCAPE") -> dict:
    return {"center": {"latitude": LAT + dlat, "longitude": LON + dlon}, "orientation": orientation,
            "segmentIndex": segment, "yearlyEnergyDcKwh": kwh}


@pytest.fixture
def bi() -> dict:
    """Two roof segments (south 30 deg, east 20 deg) and six 400 W panels in Google's layout order."""
    return {
        "center": {"latitude": LAT, "longitude": LON},
        "boundingBox": {"sw": {"latitude": LAT - 0.0001, "longitude": LON - 0.0001},
                        "ne": {"latitude": LAT + 0.0001, "longitude": LON + 0.0001}},
        "imageryQuality": "HIGH",
        "imageryDate": {"year": 2023, "month": 6, "day": 1},
        "solarPotential": {
            "panelCapacityWatts": 400, "panelHeightMeters": 1.879, "panelWidthMeters": 1.045,
            "maxArrayPanelsCount": 6,
            "wholeRoofStats": {"areaMeters2": 120.0},
            "roofSegmentStats": [
                {"pitchDegrees": 30.0, "azimuthDegrees": 180.0, "stats": {"areaMeters2": 70.0}},
                {"pitchDegrees": 20.0, "azimuthDegrees": 90.0, "stats": {"areaMeters2": 50.0}},
            ],
            "solarPanels": [
                make_panel(0, 450.0), make_panel(0, 440.0), make_panel(1, 380.0),
                make_panel(0, 445.0), make_panel(1, 370.0), make_panel(1, 360.0),
            ],
        },
    }


@pytest.fixture
def cfg(tmp_path):
    return dataclasses.replace(CONFIG, data_dir=tmp_path)


@pytest.fixture
def fake_pvgis(monkeypatch):
    """PVGIS stand-in: 1,000 kWh/kWp a year for south, 800 otherwise, flat monthly profile.
    The list records every (tilt, aspect) that was 'requested'."""
    calls: list[tuple[int, int]] = []

    def fetch(lat, lon, tilt, aspect, cfg):
        calls.append((tilt, aspect))
        yearly = 1000.0 if aspect == 0 else 800.0
        return {"kwh_per_kwp_year": yearly, "monthly_kwh_per_kwp": [yearly / 12] * 12}

    monkeypatch.setattr("solary.production._fetch", fetch)
    return calls


@pytest.fixture
def no_pvgis(monkeypatch):
    monkeypatch.setattr("solary.production._fetch", lambda *a, **k: None)
