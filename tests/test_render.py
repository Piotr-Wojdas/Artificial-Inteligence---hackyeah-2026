"""Panel geometry, and a full render on small synthetic GeoTIFFs (no Google data involved)."""

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import transform as warp

from solary import render as R
from solary import service

CRS = "EPSG:32634"                 # UTM zone 34N (eastern Poland)
X0, Y0, PIXEL, SIZE = 500_000.0, 5_800_000.0, 0.1, 300   # a 30 m x 30 m tile


def sides(corners):
    pts = [np.array(c) for c in corners]
    return [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]


def test_flat_landscape_panel_facing_south():
    corners = R.panel_corners_m(0, 0, pitch_deg=0, azimuth_deg=180, portrait=False)
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    assert max(xs) - min(xs) == pytest.approx(1.879)   # long side along the ridge (east-west)
    assert max(ys) - min(ys) == pytest.approx(1.045)   # short side down the slope (north-south)


def test_portrait_swaps_the_sides():
    corners = R.panel_corners_m(0, 0, 0, 180, portrait=True)
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    assert max(xs) - min(xs) == pytest.approx(1.045) and max(ys) - min(ys) == pytest.approx(1.879)


def test_pitch_shortens_only_the_down_slope_side():
    a, b, c, d = sides(R.panel_corners_m(10, 20, pitch_deg=60, azimuth_deg=180, portrait=True))
    assert sorted([a, b, c, d]) == pytest.approx([1.879 * 0.5, 1.879 * 0.5, 1.045, 1.045])


def test_azimuth_rotates_the_panel():
    corners = R.panel_corners_m(0, 0, 0, 90, portrait=False)   # roof facing east: ridge runs north-south
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    assert max(xs) - min(xs) == pytest.approx(1.045) and max(ys) - min(ys) == pytest.approx(1.879)
    assert sorted(sides(R.panel_corners_m(3, 4, 25, 137, False))) == pytest.approx(
        [1.045 * np.cos(np.radians(25))] * 2 + [1.879] * 2)   # any azimuth keeps the shape


@pytest.fixture
def tile(tmp_path, bi):
    """Grey aerial image, a building mask in the middle, flux rising to the east; the fixture
    building is moved to the centre of the tile with one panel exactly there."""
    transform = from_origin(X0, Y0, PIXEL, PIXEL)
    profile = {"driver": "GTiff", "height": SIZE, "width": SIZE, "crs": CRS, "transform": transform}
    rgb = np.full((3, SIZE, SIZE), 100, np.uint8)
    mask = np.zeros((SIZE, SIZE), np.uint8)
    mask[100:200, 100:200] = 1
    flux = np.tile(np.linspace(500, 1100, SIZE, dtype=np.float32), (SIZE, 1))
    paths = {"rgb": tmp_path / "rgb.tif", "mask": tmp_path / "mask.tif", "annualFlux": tmp_path / "annualFlux.tif"}
    with rasterio.open(paths["rgb"], "w", count=3, dtype="uint8", **profile) as ds:
        ds.write(rgb)
    with rasterio.open(paths["mask"], "w", count=1, dtype="uint8", **profile) as ds:
        ds.write(mask, 1)
    with rasterio.open(paths["annualFlux"], "w", count=1, dtype="float32", **profile) as ds:
        ds.write(flux, 1)

    half = SIZE * PIXEL / 2

    def lonlat(dx, dy):
        lon, lat = warp(CRS, "EPSG:4326", [X0 + half + dx], [Y0 - half + dy])
        return {"latitude": lat[0], "longitude": lon[0]}

    bi["center"] = lonlat(0, 0)
    bi["boundingBox"] = {"sw": lonlat(-5, -5), "ne": lonlat(5, 5)}
    panels = bi["solarPotential"]["solarPanels"]
    panels[0]["center"] = lonlat(0, 0)
    for i, p in enumerate(panels[1:], 1):
        p["center"] = lonlat(-4 + 1.5 * i, 3)
    return paths, bi


def test_render_draws_flux_and_panels(tile, tmp_path):
    layers, bi = tile
    out = R.render(bi, layers, tmp_path / "out" / "panels.png", bi["solarPotential"]["solarPanels"][:1])
    im = Image.open(out).convert("RGB")
    assert im.size == (900, 900)                               # 300 px tile enlarged 3x
    assert im.getpixel((450, 450)) == R.PANEL_FILL             # the panel sits in the middle
    assert im.getpixel((10, 10)) == (100, 100, 100)            # outside the mask: untouched aerial image
    west, east = im.getpixel((330, 540)), im.getpixel((570, 540))
    assert west != (100, 100, 100) and east != west            # on the roof: flux colours, changing eastwards
    assert sum(east) > sum(west)                               # more sun = brighter


def test_confirm_image_marks_the_point_and_the_building(tile, tmp_path):
    layers, bi = tile
    c = bi["center"]
    out = R.render(bi, layers, tmp_path / "confirm.png", [], flux_alpha=0.0,
                   mark=(c["latitude"], c["longitude"]), outline=True)
    im = Image.open(out).convert("RGB")
    assert im.getpixel((450, 450)) == R.RED                    # dot on the searched point
    assert im.getpixel((300, 450)) == R.RED                    # box edge 5 m west of the centre
    assert im.getpixel((450, 600)) == R.RED                    # box edge 5 m south of the centre
    assert im.getpixel((540, 540)) == (100, 100, 100)          # no flux, no panels


def test_render_reuses_an_image_newer_than_its_layers(tile, tmp_path):
    layers, bi = tile
    out = R.render(bi, layers, tmp_path / "p.png", [])
    out.write_bytes(b"kept")
    assert R.render(bi, layers, tmp_path / "p.png", bi["solarPotential"]["solarPanels"]).read_bytes() == b"kept"


def test_service_renders_the_three_images(tile, cfg, monkeypatch):
    layers, bi = tile
    monkeypatch.setattr(service, "data_layers", lambda bi, cfg: layers)
    files = service._render_all(bi, bi["center"]["latitude"], bi["center"]["longitude"], 2, cfg)
    assert set(files) == {"confirm", "all", "selected"}
    assert files["selected"].endswith("_google_2.png") and files["all"].endswith("_all.png")
    assert all((cfg.roof_dir / name).is_file() for name in files.values())
    from solary.api import IMAGE_NAME
    assert all(IMAGE_NAME.match(name) for name in files.values())   # the API can serve every one of them
