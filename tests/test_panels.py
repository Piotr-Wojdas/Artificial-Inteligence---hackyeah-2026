import dataclasses

import pytest

from solary import panels as P
from solary.errors import SolaryError


@pytest.mark.parametrize("azimuth, name", [(0, "N"), (22, "N"), (23, "NE"), (90, "E"), (180, "S"), (225, "SW"),
                                           (270, "W"), (337, "NW"), (338, "N"), (360, "N"), (-90, "W")])
def test_compass(azimuth, name):
    assert P.compass(azimuth) == name


def test_sizes_of_the_roof(bi, cfg):
    assert P.max_panels(bi) == 6
    assert P.max_kwp(bi, cfg) == pytest.approx(2.4)
    assert P.panel_watts(bi, cfg) == 400
    assert P.energy_scale(bi, cfg) == 1.0


@pytest.mark.parametrize("kwp, n", [(0.4, 1), (0.59, 1), (0.6, 2), (1.0, 3), (1.2, 3), (2.4, 6),
                                    (0.01, 1),    # never fewer than one panel
                                    (100.0, 6)])  # never more than the roof fits
def test_panels_for_kwp_rounds_half_up_and_clamps(bi, cfg, kwp, n):
    assert P.panels_for_kwp(bi, kwp, cfg) == n


def test_panels_for_kwp_survives_float_noise(bi, cfg):
    bi["solarPotential"]["solarPanels"] *= 10  # 60 panels
    assert P.panels_for_kwp(bi, 6.0, cfg) == 15
    assert P.panels_for_kwp(bi, 5.2, cfg) == 13
    assert P.panels_for_kwp(bi, 3.0, cfg) == 8   # 7.5 panels -> 8
    assert P.panels_for_kwp(bi, 5.0, cfg) == 13  # 12.5 panels -> 13


def test_no_panels_fit(bi, cfg):
    bi["solarPotential"]["solarPanels"] = []
    assert P.max_panels(bi) == 0
    with pytest.raises(P.NoPanelsFit):
        P.panels_for_kwp(bi, 5, cfg)
    del bi["solarPotential"]["solarPanels"]  # the API may leave the list out entirely
    assert P.max_panels(bi) == 0 and P.ordered_panels(bi, cfg) == []


def test_order_google_keeps_the_api_order(bi, cfg):
    energies = [p["yearlyEnergyDcKwh"] for p in P.ordered_panels(bi, cfg)]
    assert energies == [450, 440, 380, 445, 370, 360]


def test_order_yield_sorts_best_first_without_touching_the_response(bi, cfg):
    by_yield = dataclasses.replace(cfg, panel_order="yield")
    energies = [p["yearlyEnergyDcKwh"] for p in P.ordered_panels(bi, by_yield)]
    assert energies == [450, 445, 440, 380, 370, 360]
    assert [p["yearlyEnergyDcKwh"] for p in bi["solarPotential"]["solarPanels"]][:3] == [450, 440, 380]


def test_unknown_order_is_an_error(bi, cfg):
    with pytest.raises(SolaryError):
        P.ordered_panels(bi, dataclasses.replace(cfg, panel_order="random"))


def test_panel_watts_override(bi, cfg):
    bigger = dataclasses.replace(cfg, panel_watts=450.0)
    assert P.panel_watts(bi, bigger) == 450
    assert P.energy_scale(bi, bigger) == pytest.approx(1.125)
    assert P.max_kwp(bi, bigger) == pytest.approx(2.7)
    assert P.panels_for_kwp(bi, 0.9, bigger) == 2


def test_segments_table(bi):
    rows = P.segments_table(bi)
    assert [r["segment"] for r in rows] == [0, 1]
    assert rows[0] == {"segment": 0, "facing": "S", "pitch_deg": 30.0, "azimuth_deg": 180.0, "area_m2": 70.0,
                       "max_panels": 3, "kwh_dc_per_panel": pytest.approx((450 + 440 + 445) / 3)}
    assert rows[1]["facing"] == "E" and rows[1]["max_panels"] == 3


def test_segment_without_panels_and_omitted_zero_fields(bi):
    # proto3 JSON leaves out zero values: a flat, north-facing segment has neither pitch nor azimuth
    bi["solarPotential"]["roofSegmentStats"].append({"stats": {"areaMeters2": 5.0}})
    assert P.segment_geometry(bi, 2) == (0.0, 0.0)
    row = P.segments_table(bi)[2]
    assert row["max_panels"] == 0 and row["kwh_dc_per_panel"] is None and row["facing"] == "N"


def test_distance_to_building(bi):
    assert P.distance_to_building_m(52.0, 19.0, bi) == 0.0
    assert P.distance_to_building_m(52.00005, 19.00009, bi) == 0.0           # inside the bounding box
    assert P.distance_to_building_m(52.0011, 19.0, bi) == pytest.approx(111.3, abs=0.5)  # 0.001 deg north of the box
    east = P.distance_to_building_m(52.0, 19.0011, bi)                        # 0.001 deg east of the box
    assert east == pytest.approx(68.5, abs=0.5)                              # 111.32 km x cos(52 deg)
