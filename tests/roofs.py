"""Synthetic roofs for the layout tests: the Solar API map layers (height map, mask, flux, aerial
image) as small GeoTIFFs, with a matching buildingInsights response. Roofs are drawn from planes
given in local metres (x east, y north from the middle of a 40 m tile); nothing comes from Google."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform as warp

CRS = "EPSG:32634"                       # UTM zone 34N (eastern Poland)
X0, Y0, PIXEL, SIZE = 500_000.0, 5_800_000.0, 0.1, 400   # a 40 m x 40 m tile, north-west corner at X0, Y0
HALF = SIZE * PIXEL / 2
GROUND = 250.0                           # metres above sea level


def segment(poly, pitch, azimuth, ref):
    """A roof plane over the polygon `poly`, facing `azimuth`, through the point ref = (x, y, z)."""
    return {"poly": [tuple(map(float, p)) for p in poly], "pitch": float(pitch), "azimuth": float(azimuth),
            "ref": tuple(map(float, ref))}


def rotate(points, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(x * c - y * s, x * s + y * c) for x, y in points]


def box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def flux_for(pitch, azimuth):
    """Rough yearly kWh per kW in Poland for a plane: 1000 flat, more facing south, less north."""
    return 1000.0 + 300.0 * math.sin(math.radians(pitch)) * math.cos(math.radians(azimuth - 180.0))


def _inside(poly, x, y):
    out = np.zeros(x.shape, bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
            out ^= ((y1 > y) != (y2 > y)) & (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1)
    return out


def _slopes(pitch, azimuth):
    t, az = math.tan(math.radians(pitch)), math.radians(azimuth)
    return -t * math.sin(az), -t * math.cos(az)


def _height(seg, x, y):
    a, b = _slopes(seg["pitch"], seg["azimuth"])
    rx, ry, rz = seg["ref"]
    return rz + a * (x - rx) + b * (y - ry)


def lonlat(x, y):
    lon, lat = warp(CRS, "EPSG:4326", [X0 + HALF + x], [Y0 - HALF + y])
    return {"latitude": lat[0], "longitude": lon[0]}


def build(folder: Path, segments: list[dict], obstacles=(), google: list[dict] | None = None, noise: float = 0.0,
          height_error: float = 0.0, seed: int = 0) -> tuple[dict, dict[str, Path]]:
    """Layers and a buildingInsights response for a building made of `segments`.
    obstacles: (polygon, height above the roof) - chimneys, dormers, parapets; they shade the roof
    north of them. google: Google's segments when they differ from the real planes (e.g. a flat
    roof split in two); by default one per real plane. noise: std of the height map in metres.
    height_error: added to Google's plane heights."""
    rng = np.random.default_rng(seed)
    cols, rows = np.meshgrid(np.arange(SIZE), np.arange(SIZE))
    x, y = -HALF + (cols + 0.5) * PIXEL, HALF - (rows + 0.5) * PIXEL
    dsm = np.full((SIZE, SIZE), GROUND)
    flux = np.full((SIZE, SIZE), 900.0)
    mask = np.zeros((SIZE, SIZE), bool)
    rgb = np.zeros((3, SIZE, SIZE), np.uint8) + np.array([110, 120, 100], np.uint8)[:, None, None]
    for k, seg in enumerate(segments):
        inside = _inside(seg["poly"], x, y)
        dsm[inside] = _height(seg, x[inside], y[inside])
        flux[inside] = flux_for(seg["pitch"], seg["azimuth"])
        mask |= inside
        rgb[:, inside] = np.array([150 + 10 * (k % 3), 70 + 15 * (k % 4), 60], np.uint8)[:, None]
    for poly, height in obstacles:
        inside = _inside(poly, x, y)
        dsm[inside] += height
        flux[inside] = 700.0
        rgb[:, inside] = 70
        shade = np.zeros_like(inside)
        for d in np.linspace(0.2, 1.5 * height, 6):        # the shadow falls north of it
            shade |= _inside(poly, x, y - d)
        flux[shade & mask & ~inside] *= 0.8
    dsm += rng.normal(0.0, noise, dsm.shape) if noise else 0.0

    folder.mkdir(parents=True, exist_ok=True)
    profile = {"driver": "GTiff", "height": SIZE, "width": SIZE, "crs": CRS, "transform": from_origin(X0, Y0, PIXEL, PIXEL)}
    layers = {n: folder / f"{n}.tif" for n in ("rgb", "mask", "annualFlux", "dsm")}
    with rasterio.open(layers["rgb"], "w", count=3, dtype="uint8", **profile) as ds:
        ds.write(rgb)
    with rasterio.open(layers["mask"], "w", count=1, dtype="uint8", **profile) as ds:
        ds.write(mask.astype(np.uint8), 1)
    with rasterio.open(layers["annualFlux"], "w", count=1, dtype="float32", **profile) as ds:
        ds.write(flux.astype(np.float32), 1)
    with rasterio.open(layers["dsm"], "w", count=1, dtype="float32", **profile) as ds:
        ds.write(dsm.astype(np.float32), 1)

    stats = []
    for seg in google or segments:
        xs, ys = [p[0] for p in seg["poly"]], [p[1] for p in seg["poly"]]
        inside = _inside(seg["poly"], x, y)
        cx, cy = float(x[inside].mean()), float(y[inside].mean())
        stats.append({"pitchDegrees": seg["pitch"], "azimuthDegrees": seg["azimuth"],
                      "stats": {"areaMeters2": inside.sum() * PIXEL ** 2 / math.cos(math.radians(seg["pitch"]))},
                      "center": lonlat(cx, cy),
                      "boundingBox": {"sw": lonlat(min(xs), min(ys)), "ne": lonlat(max(xs), max(ys))},
                      "planeHeightAtCenterMeters": float(_height(seg, cx, cy)) + height_error})
    every = [p for seg in segments for p in seg["poly"]]
    bi = {
        "center": lonlat(0, 0),
        "boundingBox": {"sw": lonlat(min(p[0] for p in every), min(p[1] for p in every)),
                        "ne": lonlat(max(p[0] for p in every), max(p[1] for p in every))},
        "imageryQuality": "HIGH", "imageryDate": {"year": 2023, "month": 6, "day": 1},
        "solarPotential": {
            "panelCapacityWatts": 400, "panelHeightMeters": 1.879, "panelWidthMeters": 1.045,
            "wholeRoofStats": {"areaMeters2": sum(s["stats"]["areaMeters2"] for s in stats)},
            "roofSegmentStats": stats,
            # stand-in for Google's own layout: two panels in the middle of every segment
            "solarPanels": [{"center": s["center"], "orientation": "LANDSCAPE", "segmentIndex": i,
                             "yearlyEnergyDcKwh": 0.4 * flux_for(s["pitchDegrees"], s["azimuthDegrees"])}
                            for i, s in enumerate(stats) for _ in range(2)],
        },
    }
    bi["solarPotential"]["maxArrayPanelsCount"] = len(bi["solarPotential"]["solarPanels"])
    return bi, layers


# ------------------------------------------------------------------ roofs used in the tests
def gable(chimney=True):
    """12 m x 9 m house, ridge east-west, 35 deg; a chimney on the south side."""
    ridge = GROUND + 9.0
    segs = [segment(box(-6, -4.5, 6, 0), 35, 180, (0, 0, ridge)), segment(box(-6, 0, 6, 4.5), 35, 0, (0, 0, ridge))]
    return segs, ([(box(1.5, -2.6, 2.3, -1.8), 1.2)] if chimney else [])


def hip():
    """14 m x 10 m hip roof, 30 deg, short ridge in the middle."""
    top = GROUND + 7.0 + 5 * math.tan(math.radians(30))
    return [segment([(-7, -5), (7, -5), (2, 0), (-2, 0)], 30, 180, (0, 0, top)),
            segment([(-7, 5), (-2, 0), (2, 0), (7, 5)], 30, 0, (0, 0, top)),
            segment([(7, -5), (7, 5), (2, 0)], 30, 90, (2, 0, top)),
            segment([(-7, -5), (-2, 0), (-7, 5)], 30, 270, (-2, 0, top))], []


def flat(turn=20.0):
    """20 m x 14 m flat roof turned `turn` degrees, with a parapet and an air-conditioning unit.
    Google splits it into two segments with slightly different (wrong) pitches."""
    level = GROUND + 12.0
    real = [segment(rotate(box(-10, -7, 10, 7), turn), 1.0, 180, (0, 0, level))]
    google = [segment(rotate(box(-10, -7, 0, 7), turn), 1.5, 200, (-5, 0, level)),
              segment(rotate(box(0, -7, 10, 7), turn), 0.5, 160, (5, 0, level))]
    parapet = 0.3
    walls = [box(-10, -7, 10, -7 + parapet), box(-10, 7 - parapet, 10, 7),
             box(-10, -7, -10 + parapet, 7), box(10 - parapet, -7, 10, 7)]
    obstacles = [(rotate(w, turn), 0.6) for w in walls] + [(rotate(box(1, -1, 3, 0.5), turn), 1.5)]
    return real, obstacles, google


def two_levels():
    """An L of two flat roofs 3.5 m apart in height: no panel may span the step."""
    return [segment(box(-9, -2, 3, 6), 0, 180, (0, 0, GROUND + 15.0)),
            segment(box(3, -6, 9, 6), 0, 180, (0, 0, GROUND + 11.5))], []


def steep():
    """A mansard: a steep 70 deg lower slope (no panels) under a 20 deg upper one."""
    return [segment(box(-6, -5, 6, -3.5), 70, 180, (0, -3.5, GROUND + 9.0)),
            segment(box(-6, -3.5, 6, 0), 20, 180, (0, 0, GROUND + 9.0 + 3.5 * math.tan(math.radians(20))))], []
