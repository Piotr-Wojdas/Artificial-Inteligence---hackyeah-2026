"""PNG images of the roof from the Solar API map layers: "is this your house?" and the panel layout.

The aerial image, the building mask and the annual solar flux come as GeoTIFFs in a metric
(UTM) projection, so panel rectangles are drawn in metres and mapped to pixels with the
raster's affine transform. The images contain Google imagery: they live in the same cache
folder as the rest of the Solar data and are deleted with it after `cfg.cache_days`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .panels import segment_geometry

PANEL_H_M, PANEL_W_M = 1.879, 1.045  # Google's panel when the response does not say otherwise
MIN_SIDE_PX, MAX_SIDE_PX = 900, 1600  # small rasters are enlarged, very large ones reduced
RED, WHITE = (255, 40, 40), (255, 255, 255)
PANEL_FILL, PANEL_EDGE = (0, 60, 120), (0, 255, 255)


def _colormap(v: np.ndarray) -> np.ndarray:
    """0..1 -> RGB (dark blue -> purple -> orange -> yellow)."""
    stops = np.array([[20, 20, 90], [120, 40, 140], [230, 100, 40], [255, 230, 60]], float)
    x = np.clip(v, 0, 1) * (len(stops) - 1)
    i = np.minimum(x.astype(int), len(stops) - 2)
    f = (x - i)[..., None]
    return stops[i] * (1 - f) + stops[i + 1] * f


def _pixel(to_px, x: float, y: float) -> tuple[float, float]:
    """Map coordinates (metres) -> pixel (column, row) with the raster's inverse affine transform."""
    return to_px.a * x + to_px.b * y + to_px.c, to_px.d * x + to_px.e * y + to_px.f


def panel_corners_m(x: float, y: float, pitch_deg: float, azimuth_deg: float, portrait: bool,
                    height_m: float = PANEL_H_M, width_m: float = PANEL_W_M) -> list[tuple[float, float]]:
    """Plan-view corners (east, north in metres) of a panel centred at (x, y) on a roof plane.
    The side that runs down the slope is foreshortened by cos(pitch)."""
    az, pitch = np.radians(azimuth_deg), np.radians(pitch_deg)
    along = (height_m if portrait else width_m) / 2 * np.cos(pitch)
    across = (width_m if portrait else height_m) / 2
    down = np.array([np.sin(az), np.cos(az)])    # down-slope direction
    ridge = np.array([np.cos(az), -np.sin(az)])  # along the ridge
    return [tuple(np.array([x, y]) + a * along * down + b * across * ridge)
            for a, b in ((1, 1), (1, -1), (-1, -1), (-1, 1))]


def render(bi: dict, layers: dict[str, Path], out: Path, panels: list[dict], flux_alpha: float = 0.55,
           mark: tuple[float, float] | None = None, outline: bool = False) -> Path:
    """Aerial photo with the roof's annual solar flux and the given `panels` drawn on it.
    For the "is this your house?" image: panels=[], flux_alpha=0, mark=(lat, lon) of the
    searched point (red dot) and outline=True (red box around the building found)."""
    import rasterio
    from PIL import Image, ImageDraw
    from rasterio.warp import transform as warp

    if out.exists() and out.stat().st_mtime >= max(p.stat().st_mtime for p in layers.values()):
        return out  # already rendered from these layers

    with rasterio.open(layers["rgb"]) as ds:
        rgb = np.moveaxis(ds.read([1, 2, 3]), 0, -1).astype(np.uint8)
        crs, to_px = ds.crs, ~ds.transform
    shape = rgb.shape[:2]
    img = rgb.astype(float)
    if flux_alpha > 0:
        with rasterio.open(layers["mask"]) as ds:
            mask = ds.read(1, out_shape=shape) > 0
        with rasterio.open(layers["annualFlux"]) as ds:
            flux = ds.read(1, out_shape=shape).astype(float)
        roof = mask & np.isfinite(flux) & (flux > 0)  # outside valid data the flux is -9999
        if roof.any():
            lo, hi = np.percentile(flux[roof], [5, 99])
            colour = _colormap((flux - lo) / max(hi - lo, 1e-6))
            img[roof] = img[roof] * (1 - flux_alpha) + colour[roof] * flux_alpha
    im = Image.fromarray(img.astype(np.uint8))
    draw = ImageDraw.Draw(im)
    unit = max(1, max(im.size) // 300)  # line / marker size that stays visible on large images

    sp = bi["solarPotential"]
    if outline:
        sw, ne = bi["boundingBox"]["sw"], bi["boundingBox"]["ne"]
        bx, by = warp("EPSG:4326", crs, [sw["longitude"], ne["longitude"]], [sw["latitude"], ne["latitude"]])
        (x0, y0), (x1, y1) = _pixel(to_px, bx[0], by[0]), _pixel(to_px, bx[1], by[1])
        draw.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], outline=RED, width=2 * unit)
    if mark is not None:
        mx, my = warp("EPSG:4326", crs, [mark[1]], [mark[0]])
        px, py = _pixel(to_px, mx[0], my[0])
        r = 6 * unit
        draw.ellipse([px - r, py - r, px + r, py + r], fill=RED, outline=WHITE, width=unit)

    if panels:
        height_m = float(sp.get("panelHeightMeters", PANEL_H_M))
        width_m = float(sp.get("panelWidthMeters", PANEL_W_M))
        xs, ys = warp("EPSG:4326", crs, [p["center"]["longitude"] for p in panels],
                      [p["center"]["latitude"] for p in panels])
        for p, x, y in zip(panels, xs, ys):
            pitch, azimuth = segment_geometry(bi, int(p.get("segmentIndex", 0)))
            azimuth = float(p.get("azimuthDegrees", azimuth))   # our own layout turns panels on flat roofs
            corners = panel_corners_m(x, y, pitch, azimuth, p.get("orientation", "LANDSCAPE") == "PORTRAIT",
                                      height_m, width_m)
            draw.polygon([_pixel(to_px, *c) for c in corners], outline=PANEL_EDGE, fill=PANEL_FILL)

    side = max(im.size)
    if side < MIN_SIDE_PX:
        k = MIN_SIDE_PX // side + (MIN_SIDE_PX % side > 0)
        im = im.resize((im.width * k, im.height * k), Image.NEAREST)
    elif side > MAX_SIDE_PX:
        k = MAX_SIDE_PX / side
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out
