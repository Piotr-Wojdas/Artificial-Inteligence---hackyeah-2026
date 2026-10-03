"""Our own panel layout on synthetic roofs (tests/roofs.py): where the panels go, that they keep
clear of edges, obstacles and steps, how they are ordered, and how analyze() uses them."""

import dataclasses
import json
import math

import numpy as np
import pytest
from rasterio.warp import transform as warp

import roofs
from solary import layout as L
from solary import service
from solary.errors import SolaryError
from solary.render import panel_corners_m
from solary.solar_api import SolarApiError

TOL = 0.12   # the layers have 0.1 m pixels: a panel edge may be off by about one


@pytest.fixture
def build(tmp_path):
    def make(case, **kwargs):
        segments, obstacles, *google = case
        return roofs.build(tmp_path / "layers", segments, obstacles, google=google[0] if google else None, **kwargs)
    return make


def local(panels):
    """Panel centres in the tile's local metres (x east, y north from its middle)."""
    xs, ys = warp("EPSG:4326", roofs.CRS, [p["center"]["longitude"] for p in panels],
                  [p["center"]["latitude"] for p in panels])
    return [(x - roofs.X0 - roofs.HALF, y - roofs.Y0 + roofs.HALF) for x, y in zip(xs, ys)]


def outlines(bi, panels):
    """Plan-view corners of every panel."""
    sp = bi["solarPotential"]
    out = []
    for p, (x, y) in zip(panels, local(panels)):
        pitch = sp["roofSegmentStats"][p["segmentIndex"]]["pitchDegrees"]
        out.append(panel_corners_m(x, y, pitch if pitch >= L.FLAT_PITCH_DEG else 0.0, p["azimuthDegrees"],
                                   p["orientation"] == "PORTRAIT", sp["panelHeightMeters"], sp["panelWidthMeters"]))
    return out


def samples(corners, n=6):
    """Points along the edges of a panel."""
    return [tuple(np.array(a) + (np.array(b) - np.array(a)) * k / n)
            for a, b in zip(corners, corners[1:] + corners[:1]) for k in range(n)]


def clearance(point, poly):
    """Distance from the point to the polygon's outline: positive inside, negative outside."""
    p, d = np.array(point, float), np.inf
    for a, b in zip(poly, poly[1:] + poly[:1]):
        a, b = np.array(a, float), np.array(b, float)
        t = np.clip(np.dot(p - a, b - a) / np.dot(b - a, b - a), 0, 1)
        d = min(d, float(np.linalg.norm(p - a - t * (b - a))))
    return d if roofs._inside(poly, p[:1], p[1:])[0] else -d


def no_overlaps(shapes):
    """No point of the plan is covered by two panels (checked on a 5 cm grid)."""
    x, y = np.meshgrid(np.arange(-20, 20, 0.05), np.arange(-20, 20, 0.05))
    cover = np.zeros(x.shape, int)
    for corners in shapes:
        cover += roofs._inside(list(corners), x, y)
    return cover.max() <= 1


def plan_margin(cfg, pitch):
    return cfg.layout_margin_m * math.cos(math.radians(pitch))


# ------------------------------------------------------------------ geometry
def test_gable_roof_keeps_clear_of_edges_ridge_and_chimney(build, cfg):
    bi, layers = build(roofs.gable())
    panels = L.own_panels(bi, layers, cfg)
    segments, obstacles = roofs.gable()
    by_segment = [sum(p["segmentIndex"] == i for p in panels) for i in range(2)]
    assert by_segment == [20, 24]                  # 6 x 4 a side; the chimney costs the south side 4
    assert {p["orientation"] for p in panels} == {"LANDSCAPE"}
    chimney = obstacles[0][0]
    shapes = outlines(bi, panels)
    for p, corners in zip(panels, shapes):
        own = segments[p["segmentIndex"]]["poly"]
        for point in samples(corners):
            assert clearance(point, own) > plan_margin(cfg, 35) - TOL       # on its own side, clear of the ridge
            assert clearance(point, chimney) < -(cfg.layout_margin_m - TOL)
    assert no_overlaps(shapes)


def test_more_margin_fewer_panels(build, cfg):
    bi, layers = build(roofs.gable(chimney=False))
    count = {m: len(L.own_panels(bi, layers, dataclasses.replace(cfg, layout_margin_m=m))) for m in (0.0, 0.2, 0.5)}
    assert count == {0.0: 60, 0.2: 48, 0.5: 40}    # 6 x 5, 6 x 4 and 5 x 4 a side


def test_hip_roof_panels_stay_on_their_own_segment(build, cfg):
    bi, layers = build(roofs.hip())
    panels = L.own_panels(bi, layers, cfg)
    segments, _ = roofs.hip()
    assert {p["segmentIndex"] for p in panels} == {0, 1, 2, 3}
    for p, corners in zip(panels, outlines(bi, panels)):
        for point in samples(corners):
            assert clearance(point, segments[p["segmentIndex"]]["poly"]) > plan_margin(cfg, 30) - TOL
    south = [p["yearlyEnergyDcKwh"] for p in panels if p["segmentIndex"] == 0]
    north = [p["yearlyEnergyDcKwh"] for p in panels if p["segmentIndex"] == 1]
    assert min(south) > max(north)


def test_flat_roof_split_by_google_is_one_field_along_its_outline(build, cfg):
    bi, layers = build(roofs.flat(turn=20))
    data = L.read_layers(layers)
    frames = L.segment_frames(bi, data["crs"])
    L.segment_owner(frames, data, cfg)
    assert L.segment_groups(frames, cfg) == [[0, 1]]
    panels = L.own_panels(bi, layers, cfg)
    assert len(panels) >= 100
    assert {round(p["azimuthDegrees"] % 90) for p in panels} == {70}   # the building is turned 20 deg
    assert {p["segmentIndex"] for p in panels} == {0, 1}                # panels across Google's split
    real, obstacles, _ = roofs.flat(turn=20)
    shapes = outlines(bi, panels)
    for corners in shapes:
        for point in samples(corners):
            for wall, _ in obstacles:                                    # parapet and air conditioning
                assert clearance(point, wall) < -(cfg.layout_margin_m - TOL)
    assert no_overlaps(shapes)


def test_no_panel_spans_a_step_between_two_flat_roofs(build, cfg):
    bi, layers = build(roofs.two_levels())
    panels = L.own_panels(bi, layers, cfg)
    segments, _ = roofs.two_levels()
    assert [sum(p["segmentIndex"] == i for p in panels) for i in range(2)] == [42, 30]
    for p, corners in zip(panels, outlines(bi, panels)):
        for point in samples(corners):
            assert clearance(point, segments[p["segmentIndex"]]["poly"]) > cfg.layout_margin_m - TOL


def test_steep_segments_get_no_panels(build, cfg):
    bi, layers = build(roofs.steep())
    panels = L.own_panels(bi, layers, cfg)
    assert len(panels) == 18 and {p["segmentIndex"] for p in panels} == {1}
    allowed = dataclasses.replace(cfg, layout_max_pitch_deg=75)
    assert {p["segmentIndex"] for p in L.own_panels(bi, layers, allowed)} == {0, 1}


def test_spare_room_is_split_evenly(tmp_path, cfg):
    """10.5 m x 5 m flat roof: 5 panels a row leave about 0.6 m, half on each side."""
    roof = [roofs.segment(roofs.box(-5.25, -2.5, 5.25, 2.5), 0, 180, (0, 0, roofs.GROUND + 6))]
    bi, layers = roofs.build(tmp_path / "small", roof)
    xs = [x for x, _ in local(L.own_panels(bi, layers, cfg))]
    left, right = min(xs) - 1.879 / 2 + 5.25, 5.25 - max(xs) - 1.879 / 2
    assert left == pytest.approx(right, abs=0.11)


def test_robust_to_height_noise_and_googles_plane_height(build, cfg):
    clean = L.own_panels(*build(roofs.gable()), cfg)
    for noise, error in ((0.05, 0.0), (0.03, 0.4), (0.0, -0.8)):
        noisy = L.own_panels(*build(roofs.gable(), noise=noise, height_error=error), cfg)
        assert len(noisy) == len(clean), (noise, error)


def test_energy_is_the_mean_flux_under_the_panel(build, cfg):
    bi, layers = build(roofs.steep())
    panels = L.own_panels(bi, layers, cfg)
    assert [p["yearlyEnergyDcKwh"] for p in panels] == pytest.approx([0.4 * roofs.flux_for(20, 180)] * 18, abs=0.01)


def test_bigger_panels(build, cfg):
    bi, layers = build(roofs.gable())
    big = dataclasses.replace(cfg, panel_size_m=(2.278, 1.134))
    assert len(L.own_panels(bi, layers, big)) < len(L.own_panels(bi, layers, cfg))
    assert L.panel_size(bi, big) == (2.278, 1.134) and L.panel_size(bi, cfg) == (1.879, 1.045)


# ------------------------------------------------------------------ order
def test_order_is_best_first_and_grows_as_one_array(build, cfg):
    bi, layers = build(roofs.gable(chimney=False))
    panels = L.own_panels(bi, layers, cfg)
    energy = [p["yearlyEnergyDcKwh"] for p in panels]
    assert energy[0] == max(energy)
    assert all(p["segmentIndex"] == 0 for p in panels[:24])            # the whole south side before the north
    centres = local(panels[:12])
    for k in range(1, 12):                                               # every panel touches one chosen before
        assert min(math.dist(centres[k], c) for c in centres[:k]) < 2.0


def test_without_compactness_the_order_is_strictly_best_first(build, cfg):
    bi, layers = build(roofs.hip())
    energy = [p["yearlyEnergyDcKwh"] for p in L.own_panels(bi, layers, dataclasses.replace(cfg, layout_compactness=0))]
    assert energy == sorted(energy, reverse=True)


def test_outline_azimuth():
    pts = np.array(roofs.rotate(roofs.box(-10, -3, 10, 3), 30))
    x, y = np.meshgrid(np.linspace(-12, 12, 241), np.linspace(-12, 12, 241))
    inside = roofs._inside([tuple(p) for p in pts], x, y)
    az = L._outline_azimuth(x[inside], y[inside])
    assert 135 <= az < 225 and (az + 30) % 90 == pytest.approx(0, abs=0.3)


# ------------------------------------------------------------------ result, cache, errors
def test_with_own_layout_replaces_googles_panels_and_caches_them(build, cfg, monkeypatch):
    bi, layers = build(roofs.gable())
    google = json.loads(json.dumps(bi))
    own = L.with_own_layout(bi, layers, cfg)
    sp = own["solarPotential"]
    assert len(sp["solarPanels"]) == sp["maxArrayPanelsCount"] == 44
    assert bi == google                                                   # the response itself is untouched
    cached = list(cfg.roof_dir.glob("layout_*.json"))
    assert len(cached) == 1 and L.layout_key(cfg) in cached[0].name
    monkeypatch.setattr(L, "own_panels", lambda *a: pytest.fail("must come from the cache"))
    assert L.with_own_layout(bi, layers, cfg) == own
    assert L.layout_key(dataclasses.replace(cfg, layout_margin_m=0.3)) != L.layout_key(cfg)


def test_layout_unavailable(build, cfg):
    bi, layers = build(roofs.gable())
    for s in bi["solarPotential"]["roofSegmentStats"]:
        del s["planeHeightAtCenterMeters"]
    with pytest.raises(L.LayoutUnavailable):
        L.own_panels(bi, layers, cfg)


def test_layout_unavailable_without_heights(build, cfg):
    import rasterio
    bi, layers = build(roofs.gable())
    with rasterio.open(layers["dsm"], "r+") as ds:
        ds.write(np.full((roofs.SIZE, roofs.SIZE), -9999.0, np.float32), 1)
    with pytest.raises(L.LayoutUnavailable):
        L.own_panels(bi, layers, cfg)


# ------------------------------------------------------------------ analyze()
@pytest.fixture
def own(build, cfg, monkeypatch, fake_pvgis):
    bi, layers = build(roofs.gable())
    asked = []

    def data_layers(bi, cfg, names=("rgb", "mask", "annualFlux")):
        asked.append(tuple(names))
        return {n: layers[n] for n in names}

    monkeypatch.setattr(service, "building_insights", lambda lat, lon, cfg: bi)
    monkeypatch.setattr(service, "data_layers", data_layers)
    return dataclasses.replace(cfg, layout="own"), asked, bi["center"]["latitude"], bi["center"]["longitude"]


def test_analyze_with_our_layout(own):
    cfg, asked, lat, lon = own
    res = service.analyze(lat=lat, lon=lon, panels=10, cfg=cfg)
    assert res["warnings"] == [] and res["building"]["max_panels"] == 44
    assert res["selected"]["panels"] == 10 and [r["segment"] for r in res["selected"]["per_segment"]] == [0]
    assert res["selected"]["kwh_year"] == pytest.approx(10 * 0.4 * roofs.flux_for(35, 180) * 0.86, rel=0.01)
    lay = res["layout"]
    assert lay["algorithm"] == "own" and lay["margin_m"] == 0.2 and lay["google_max_panels"] == 4
    assert lay["google_kwh_year"] is None                                 # the stand-in Google layout fits 4
    assert res["assumptions"]["layout"] == "own"
    assert set(asked[0]) == {"rgb", "mask", "annualFlux", "dsm"}         # one dataLayers call for everything
    from solary.api import IMAGE_NAME
    assert all(IMAGE_NAME.match(name) and "own-" in name for name in (res["images"]["all"], res["images"]["selected"]))
    assert all((cfg.roof_dir / name).is_file() for name in res["images"].values())
    json.dumps(res)


def test_analyze_compares_with_googles_layout(own):
    cfg, _, lat, lon = own
    res = service.analyze(lat=lat, lon=lon, panels=3, images=False, cfg=cfg)
    assert res["layout"]["google_kwh_year"] == pytest.approx(
        (2 * 0.4 * roofs.flux_for(35, 180) + 0.4 * roofs.flux_for(35, 0)) * 0.86)
    assert res["images"] == {}


def test_analyze_falls_back_to_googles_layout(own, monkeypatch):
    cfg, _, lat, lon = own

    def broken(*a, **k):
        raise SolarApiError("no dsm")

    monkeypatch.setattr(service, "data_layers", broken)
    res = service.analyze(lat=lat, lon=lon, images=False, cfg=cfg)
    assert res["warnings"] == ["layout_fallback"] and res["layout"] == {"algorithm": "google"}
    assert res["building"]["max_panels"] == 4 and res["assumptions"]["layout"] == "google"


def test_unknown_layout(own):
    cfg, _, lat, lon = own
    with pytest.raises(SolaryError):
        service.analyze(lat=lat, lon=lon, cfg=dataclasses.replace(cfg, layout="magic"))


def test_an_evenly_lit_roof_fills_as_one_block_from_the_middle(tmp_path, cfg):
    roof = [roofs.segment(roofs.box(-6, -4, 6, 4), 0, 180, (0, 0, roofs.GROUND + 6))]     # fits 6 x 7
    bi, layers = roofs.build(tmp_path / "even", roof)
    centres = np.array(local(L.own_panels(bi, layers, cfg)[:15]))
    assert np.abs(centres.mean(axis=0)).max() < 1.0                    # around the middle of the roof
    width, height = np.ptp(centres, axis=0)
    assert width < 3 * 1.9 and height < 5 * 1.07                         # 15 panels in a 3 x 5 block
