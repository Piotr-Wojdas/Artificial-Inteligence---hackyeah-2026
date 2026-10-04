"""Our own panel layout: where the panels go on a roof, computed from the Solar API map layers.

Google's buildingInsights already lists the panels that fit, but by Google's unpublished rules:
its own panel, no margin from edges, chimneys or ridges. This module lays the panels out
itself, so the rules are ours to set (margin, gap, panel size):

  1. Free roof. Each roof segment is a plane (centre, height, pitch, azimuth), fitted to the
     height map (DSM). A roof pixel belongs to the segment whose plane it lies on; a pixel off
     every plane is an obstacle (chimney, dormer, parapet, tree) or another level, and stays empty.
  2. Fields. A pitched segment is one field; segments on one plane (Google sometimes splits a
     roof around a dormer) and flat segments on one level are laid out together.
  3. Candidates. Panels sit on a lattice: parallel to the eaves on a pitched field, parallel to
     the outline on a flat one. Both panel orientations and every lattice offset (in steps of one
     map pixel) are tried; a panel counts when it and a margin around it lie entirely on free
     roof. A row may shift by half a panel (brick bond) when that fits one more panel in it.
  4. Energy. A panel's yearly DC energy is the mean annual flux under it (kWh per kW a year,
     shade included) times the panel's power, as Google computes it for its own panels.
  5. Order. Panels are ranked best-first with a bonus for touching panels already chosen, so the
     layout for N panels is the first N of the list and grows as compact arrays.

`with_own_layout` returns a copy of the buildingInsights response with our panels in place of
Google's, so the production estimate, the tables and the images work on it unchanged.
"""

from __future__ import annotations

import dataclasses
import hashlib
import heapq
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from affine import Affine

from .config import CONFIG, Config
from .errors import SolaryError
from .panels import google_panel_watts
from .render import PANEL_H_M, PANEL_W_M
from .solar_api import tag

VERSION = 1               # part of the cache key: bump when the algorithm changes
NODATA = -1000.0          # the layers mark pixels without data with -9999
FLAT_PITCH_DEG = 5.0      # below this the roof has no real eaves: the lattice follows the outline
COPLANAR_DEG = 3.0        # pitched segments whose planes differ by less are one field
BOX_PAD_M = 0.2           # a segment may own pixels this far outside Google's bounding box
NEAR_M = 1.0              # first guess of the plane's height: pixels this close to Google's plane
FIT_BAND_M = 0.3          # pixels this close to the plane are used to refit it to the DSM
MIN_FIT_PIXELS = 50       # fewer pixels than this: Google's plane is kept as given
MAX_REFIT_TURN_DEG = 3.0  # a refit turning the plane more than this is rejected


class LayoutUnavailable(SolaryError):
    """The map layers do not allow our layout here (no segment positions or no height data)."""


def layout_key(cfg: Config = CONFIG) -> str:
    """Short id of the settings that change our layout: part of the cache and image file names."""
    parts = (VERSION, cfg.layout, cfg.layout_margin_m, cfg.layout_gap_m, cfg.layout_height_tol_m, cfg.layout_max_pitch_deg,
             cfg.layout_compactness, cfg.layout_shift_rows, cfg.panel_size_m)
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:10]


def panel_size(bi: dict, cfg: Config = CONFIG) -> tuple[float, float]:
    """(height, width) of the panel in metres: cfg.panel_size_m or the one Google laid out."""
    if cfg.panel_size_m:
        return float(cfg.panel_size_m[0]), float(cfg.panel_size_m[1])
    sp = bi["solarPotential"]
    return float(sp.get("panelHeightMeters", PANEL_H_M)), float(sp.get("panelWidthMeters", PANEL_W_M))


def with_own_layout(bi: dict, layers: dict[str, Path], cfg: Config = CONFIG) -> dict:
    """The buildingInsights response `bi` with our panels in place of Google's. `layers` are the
    "mask", "annualFlux" and "dsm" GeoTIFFs. The layout is cached in cfg.roof_dir (it is derived
    from Google data, so it expires with them) and recomputed when the layers are newer."""
    c = bi["center"]
    path = cfg.roof_dir / f"layout_{tag(c['latitude'], c['longitude'])}_{layout_key(cfg)}.json"
    if path.exists() and path.stat().st_mtime >= max(p.stat().st_mtime for p in layers.values()):
        panels = json.loads(path.read_text(encoding="utf-8"))
    else:
        panels = own_panels(bi, layers, cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(panels), encoding="utf-8")
    height_m, width_m = panel_size(bi, cfg)
    sp = {k: v for k, v in bi["solarPotential"].items() if k != "solarPanelConfigs"}  # those are Google's
    sp.update(solarPanels=panels, maxArrayPanelsCount=len(panels), panelHeightMeters=height_m,
              panelWidthMeters=width_m)
    return {**bi, "solarPotential": sp}


def own_panels(bi: dict, layers: dict[str, Path], cfg: Config = CONFIG) -> list[dict]:
    """Every panel that fits by our rules, in the order they are used, in the format of Google's
    solarPanels plus "azimuthDegrees" (the lattice direction, which on a flat roof is ours)."""
    from rasterio.warp import transform as warp

    data = read_layers(layers)
    frames = segment_frames(bi, data["crs"])
    if not any(frames):
        raise LayoutUnavailable("the roof segments have no position or height")
    owner = segment_owner(frames, data, cfg)
    if not any(f is not None and "plane" in f for f in frames):
        raise LayoutUnavailable("the height map does not match the roof segments")
    kw = google_panel_watts(bi) / 1000    # energy is per Google's wattage; cfg.panel_watts rescales it later
    size = panel_size(bi, cfg)

    # For 'pro' layout, strictly align rows (no staggered brick shift) and use block-filling ordering
    shift_rows = False if cfg.layout == "pro" else cfg.layout_shift_rows
    cfg_groups = cfg if shift_rows == cfg.layout_shift_rows else dataclasses.replace(cfg, layout_shift_rows=shift_rows)
    cands: list[dict] = []
    for members in segment_groups(frames, cfg_groups):
        cands += _group_candidates(members, frames, owner, data, size, kw, cfg_groups)
    if not cands:
        return []
    order = _order_pro(cands) if cfg.layout == "pro" else _order(cands, cfg.layout_compactness, cfg.layout_gap_m)
    lons, lats = warp(data["crs"], "EPSG:4326", [cands[i]["x"] for i in order], [cands[i]["y"] for i in order])
    return [{"center": {"latitude": lat, "longitude": lon},
             "orientation": "PORTRAIT" if cands[i]["portrait"] else "LANDSCAPE",
             "segmentIndex": cands[i]["segment"],
             "yearlyEnergyDcKwh": round(cands[i]["energy"], 2),
             "azimuthDegrees": round(cands[i]["azimuth"] % 360, 2)}
            for i, lon, lat in zip(order, lons, lats)]


# ------------------------------------------------------------------ layers
def read_layers(layers: dict[str, Path]) -> dict:
    """Mask, heights and flux on the height map's grid, with its transform, CRS and pixel size.
    Pixels without data are left out of the mask and get no flux."""
    import rasterio
    from rasterio.enums import Resampling

    with rasterio.open(layers["dsm"]) as ds:
        dsm = ds.read(1).astype(np.float64)
        t, crs = ds.transform, ds.crs
    with rasterio.open(layers["mask"]) as ds:
        mask = ds.read(1, out_shape=dsm.shape, resampling=Resampling.nearest) > 0
    with rasterio.open(layers["annualFlux"]) as ds:
        flux = ds.read(1, out_shape=dsm.shape, resampling=Resampling.nearest).astype(np.float64)
    valid = np.isfinite(dsm) & (dsm > NODATA) & np.isfinite(flux) & (flux > NODATA)
    if not (mask & valid).any():
        raise LayoutUnavailable("the height map has no data on this roof")
    return {"mask": mask & valid, "dsm": np.where(valid, dsm, np.nan), "flux": np.where(valid, np.maximum(flux, 0), 0.0),
            "transform": t, "crs": crs, "res": math.sqrt(abs(t.a * t.e - t.b * t.d))}


def _apply(t, x, y):
    """The affine transform `t` applied to (x, y): pixel (column, row) -> map metres, or with an
    inverted transform map metres -> pixel."""
    return t.a * x + t.b * y + t.c, t.d * x + t.e * y + t.f


def _centres(t, rows: np.ndarray, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map coordinates (metres) of pixel centres."""
    return _apply(t, cols + 0.5, rows + 0.5)


def _majority(mask: np.ndarray) -> np.ndarray:
    """3 x 3 majority filter: fills single-pixel holes (height noise) and drops stray pixels."""
    m = np.pad(mask, 1).astype(np.int8)
    h, w = mask.shape
    return sum(m[1 + dr:1 + dr + h, 1 + dc:1 + dc + w] for dr in (-1, 0, 1) for dc in (-1, 0, 1)) >= 5


# ------------------------------------------------------------------ 1. free roof per segment
def segment_frames(bi: dict, crs) -> list[dict | None]:
    """Per roof segment: centre and bounding box in map metres, pitch, azimuth and Google's plane
    height at the centre; None for a segment without them."""
    from rasterio.warp import transform as warp

    frames: list[dict | None] = []
    for s in bi["solarPotential"].get("roofSegmentStats", []):
        c, box = s.get("center"), s.get("boundingBox")
        if not c or not box or "planeHeightAtCenterMeters" not in s:
            frames.append(None)
            continue
        sw, ne = box["sw"], box["ne"]
        xs, ys = warp("EPSG:4326", crs, [c["longitude"], sw["longitude"], ne["longitude"], sw["longitude"], ne["longitude"]],
                      [c["latitude"], sw["latitude"], ne["latitude"], ne["latitude"], sw["latitude"]])
        frames.append({"x": xs[0], "y": ys[0], "box": (min(xs[1:]), max(xs[1:]), min(ys[1:]), max(ys[1:])),
                       "pitch": float(s.get("pitchDegrees", 0.0)), "azimuth": float(s.get("azimuthDegrees", 0.0)),
                       "height": float(s["planeHeightAtCenterMeters"])})
    return frames


def _slopes(pitch_deg: float, azimuth_deg: float) -> tuple[float, float]:
    """Height change per metre east and per metre north of a plane facing `azimuth_deg`."""
    t, az = math.tan(math.radians(pitch_deg)), math.radians(azimuth_deg)
    return -t * math.sin(az), -t * math.cos(az)


def segment_owner(frames: list[dict | None], data: dict, cfg: Config = CONFIG) -> np.ndarray:
    """For every pixel the index of the roof segment whose plane it lies on, or -1: not this
    building's roof, or something standing on it (obstacle). Each frame gets its "plane":
    (height at the centre, slope east, slope north) fitted to the DSM."""
    mask, dsm, t = data["mask"], data["dsm"], data["transform"]
    inv = ~t
    owner = np.full(mask.shape, -1, np.int32)
    best = np.full(mask.shape, np.inf)
    for i, f in enumerate(frames):
        if f is None:
            continue
        x0, x1, y0, y1 = f["box"]
        x0, x1, y0, y1 = x0 - BOX_PAD_M, x1 + BOX_PAD_M, y0 - BOX_PAD_M, y1 + BOX_PAD_M
        cols, rows = _apply(inv, np.array([x0, x0, x1, x1]), np.array([y0, y1, y0, y1]))
        r0, r1 = max(int(min(rows)), 0), min(int(math.ceil(max(rows))), mask.shape[0])
        c0, c1 = max(int(min(cols)), 0), min(int(math.ceil(max(cols))), mask.shape[1])
        if r0 >= r1 or c0 >= c1:
            continue
        x, y = _centres(t, *np.mgrid[r0:r1, c0:c1])
        z = dsm[r0:r1, c0:c1]
        roof = mask[r0:r1, c0:c1] & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
        dx, dy = x - f["x"], y - f["y"]
        a, b = _slopes(f["pitch"], f["azimuth"])
        resid = z - (f["height"] + a * dx + b * dy)
        near = roof & (np.abs(resid) < NEAR_M)
        if not near.any() and roof.any():     # heights on another datum: start from the roof's median
            resid = resid - np.median(resid[roof])
            near = roof & (np.abs(resid) < NEAR_M)
        if not near.any():
            continue
        height = float(np.median((z - a * dx - b * dy)[near]))
        height, a, b = f["plane"] = _refit(z, roof, dx, dy, height, a, b, cfg.layout_height_tol_m)
        off = np.abs(z - (height + a * dx + b * dy))
        take = roof & (off < cfg.layout_height_tol_m) & (off < best[r0:r1, c0:c1])
        owner[r0:r1, c0:c1][take] = i
        best[r0:r1, c0:c1][take] = off[take]
    return owner


def _refit(z: np.ndarray, roof: np.ndarray, dx: np.ndarray, dy: np.ndarray, height: float, a: float, b: float,
           tol: float) -> tuple[float, float, float]:
    """Google's plane fitted to the DSM (it can be a few cm or a fraction of a degree off, which
    matters at the far end of a long roof). Kept as given when the fit has too few pixels or
    turns the plane noticeably."""
    given = np.array([-a, -b, 1.0])                   # normal of Google's plane
    for band in (FIT_BAND_M, 2 * tol):
        resid = z - (height + a * dx + b * dy)
        sel = roof & (np.abs(resid) < band)
        if sel.sum() < MIN_FIT_PIXELS:
            break
        m = np.column_stack([dx[sel], dy[sel], np.ones(int(sel.sum()))])
        (da, db, dc), *_ = np.linalg.lstsq(m, resid[sel], rcond=None)
        fitted = np.array([-(a + da), -(b + db), 1.0])
        cos = float(given @ fitted / np.linalg.norm(given) / np.linalg.norm(fitted))
        if math.degrees(math.acos(min(1.0, cos))) > MAX_REFIT_TURN_DEG:
            break
        height, a, b = height + float(dc), a + float(da), b + float(db)
    return height, a, b


# ------------------------------------------------------------------ 2. fields
def segment_groups(frames: list[dict | None], cfg: Config = CONFIG) -> list[list[int]]:
    """Segments laid out together. Flat segments whose planes meet (Google often splits one flat
    roof into several) form one field, and so do pitched segments on one plane; a roof on another
    level stays separate, so no panel spans a step. Segments steeper than the limit get no panels."""
    usable = [i for i, f in enumerate(frames)
              if f is not None and "plane" in f and f["pitch"] <= cfg.layout_max_pitch_deg]
    parent = {i: i for i in usable}

    def root(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    def height_at(f: dict, x: float, y: float) -> float:
        height, a, b = f["plane"]
        return height + a * (x - f["x"]) + b * (y - f["y"])

    def turn(fi: dict, fj: dict) -> float:
        ni, nj = (np.array([-f["plane"][1], -f["plane"][2], 1.0]) for f in (fi, fj))
        return math.degrees(math.acos(min(1.0, float(ni @ nj / np.linalg.norm(ni) / np.linalg.norm(nj)))))

    for k, i in enumerate(usable):
        for j in usable[k + 1:]:
            fi, fj = frames[i], frames[j]
            flat = fi["pitch"] < FLAT_PITCH_DEG and fj["pitch"] < FLAT_PITCH_DEG
            if not flat and (fi["pitch"] < FLAT_PITCH_DEG or fj["pitch"] < FLAT_PITCH_DEG or turn(fi, fj) > COPLANAR_DEG):
                continue
            x, y = (fi["x"] + fj["x"]) / 2, (fi["y"] + fj["y"]) / 2     # between the centres: about where they meet
            if abs(height_at(fi, x, y) - height_at(fj, x, y)) < cfg.layout_height_tol_m:
                parent[root(i)] = root(j)
    fields: dict[int, list[int]] = {}
    for i in usable:
        fields.setdefault(root(i), []).append(i)
    return list(fields.values())


# ------------------------------------------------------------------ 3-4. candidates and energy
def _outline_azimuth(x: np.ndarray, y: np.ndarray) -> float:
    """Lattice azimuth for a flat roof: the sides of the smallest rectangle around the roof, as
    the compass azimuth closest to south (the lattice tries both panel orientations anyway)."""
    x, y = x - x.mean(), y - y.mean()

    def area(deg: float) -> float:
        c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        u, v = x * c + y * s, -x * s + y * c
        return float(np.ptp(u) * np.ptp(v))

    theta = min(np.arange(0.0, 90.0, 1.0), key=area)
    theta = min(np.arange(theta - 1.0, theta + 1.0, 0.1), key=area)
    return 135.0 + (-theta - 135.0) % 90.0    # the u axis (cos az, -sin az) runs along a side


def _local_grid(free: np.ndarray, flux: np.ndarray, t, res: float, x0: float, y0: float, azimuth: float,
                pad: float) -> dict:
    """The free pixels and flux resampled to a grid whose columns run along the eaves (u) and whose
    rows run down the slope in plan view (v), starting at (u0, v0) from the point (x0, y0)."""
    rows, cols = np.nonzero(free)
    az = math.radians(azimuth)
    eu, ed = (math.cos(az), -math.sin(az)), (math.sin(az), math.cos(az))
    px, py = _centres(t, rows, cols)
    px, py = px - x0, py - y0
    u, v = px * eu[0] + py * eu[1], px * ed[0] + py * ed[1]
    u0, v0 = float(u.min()) - pad - res / 2, float(v.min()) - pad - res / 2    # cell centres on pixel centres
    nu = int(math.ceil((float(u.max()) + pad - u0) / res)) + 1
    nv = int(math.ceil((float(v.max()) + pad - v0) / res)) + 1
    gu, gv = np.meshgrid(u0 + (np.arange(nu) + 0.5) * res, v0 + (np.arange(nv) + 0.5) * res)
    x, y = x0 + gu * eu[0] + gv * ed[0], y0 + gu * eu[1] + gv * ed[1]
    c, r = (np.floor(k).astype(int) for k in _apply(~t, x, y))
    inside = (r >= 0) & (r < free.shape[0]) & (c >= 0) & (c < free.shape[1])
    g_free, g_flux = np.zeros(gu.shape, bool), np.zeros(gu.shape)
    g_free[inside], g_flux[inside] = free[r[inside], c[inside]], flux[r[inside], c[inside]]
    return {"free": g_free, "flux": g_flux, "u0": u0, "v0": v0, "eu": eu, "ed": ed}


def _sat(a: np.ndarray) -> np.ndarray:
    """Summed-area table with a zero first row and column: box sums in four lookups."""
    s = np.zeros((a.shape[0] + 1, a.shape[1] + 1))
    s[1:, 1:] = a.cumsum(0).cumsum(1)
    return s


def _box_sum(s: np.ndarray, u0, u1, v0, v1) -> np.ndarray:
    return s[v1, u1] - s[v0, u1] - s[v1, u0] + s[v0, u0]


def _cells(start: np.ndarray, length: float, res: float, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Grid cells whose centres lie in [start, start + length): first, end (exclusive), and
    whether the span stays inside the grid. The indices are clipped so that they can be looked up."""
    first = np.ceil(start / res - 0.5).astype(int)
    end = np.ceil((start + length) / res - 0.5).astype(int)
    inside = (first >= 0) & (end <= n) & (end > first)
    return np.clip(first, 0, n), np.clip(end, 0, n), inside


def _best_lattice(grid: dict, res: float, du: float, dv: float, gap_u: float, gap_v: float, margin_u: float,
                  margin_v: float, kw: float, shift_rows: bool) -> tuple[float, list[tuple[float, float, int, float]]]:
    """The lattice of panels (du along the eaves x dv down the slope, plan view) that gives the most
    energy: (total kWh, [(u, v, row, kWh) of each panel's corner relative to the grid origin])."""
    free, flux = grid["free"], grid["flux"]
    nv, nu = free.shape
    s_free, s_flux = _sat(free), _sat(flux)
    pu, pv = du + gap_u, dv + gap_v
    n_cols, n_rows = int((nu * res - du) // pu) + 1, int((nv * res - dv) // pv) + 1
    if n_cols < 1 or n_rows < 1:
        return 0.0, []
    offsets_u = np.arange(0.0, pu, res)
    u = offsets_u[:, None] + pu * np.arange(n_cols)[None, :]              # (offset, column)
    mu0, mu1, mu_in = _cells(u - margin_u, du + 2 * margin_u, res, nu)     # panel + margin
    eu0, eu1, _ = _cells(u, du, res, nu)                                   # panel only
    a = lambda x: x[:, :, None]                                            # noqa: E731  -> (offset, column, row)
    found = []
    for offset_v in np.arange(0.0, pv, res):
        v = offset_v + pv * np.arange(n_rows)
        mv0, mv1, mv_in = _cells(v - margin_v, dv + 2 * margin_v, res, nv)
        ev0, ev1, _ = _cells(v, dv, res, nv)
        need = (a(mu1) - a(mu0)) * (mv1 - mv0)
        ok = a(mu_in) & mv_in & (_box_sum(s_free, a(mu0), a(mu1), mv0, mv1) == need)
        cells = np.maximum((a(eu1) - a(eu0)) * (ev1 - ev0), 1)
        energy = np.where(ok, _box_sum(s_flux, a(eu0), a(eu1), ev0, ev1) / cells * kw, 0.0)
        count, row_energy = ok.sum(axis=1), energy.sum(axis=1)             # (offset, row)
        aligned = _middle_of_best(row_energy.sum(axis=1), count.sum(axis=1))
        pick = np.full(n_rows, aligned)                                    # one offset for all rows...
        if shift_rows:                                                     # ...or half a panel aside (brick bond)
            half = (aligned + len(offsets_u) // 2) % len(offsets_u)       # where that fits one more panel
            pick[count[half] > count[aligned]] = half
        rows = np.arange(n_rows)
        found.append((float(row_energy[pick, rows].sum()), int(count[pick, rows].sum()),
                      [(float(u[pick[r], k]), float(v[r]), int(r), float(energy[pick[r], k, r]))
                       for r in rows for k in np.flatnonzero(ok[pick[r], :, r])]))
    best = _middle_of_best(np.array([f[0] for f in found]), np.array([f[1] for f in found]))
    return found[best][0], found[best][2]


def _middle_of_best(totals: np.ndarray, counts: np.ndarray) -> int:
    """Index of the lattice offset with the most energy. Offsets that fit as many panels within
    0.1 % of that energy count as equal and the middle one is taken, so that a lattice with room
    to spare sits in the middle of the roof rather than against one edge. Offsets wrap around."""
    top = int(np.argmax(totals))
    equal = np.flatnonzero((totals >= totals[top] * (1 - 1e-3)) & (counts >= counts[top]))
    if len(equal) == 1:
        return top
    angle = 2 * np.pi * equal / len(totals)
    middle = math.atan2(float(np.sin(angle).mean()), float(np.cos(angle).mean())) % (2 * math.pi)
    distance = np.abs((angle - middle + np.pi) % (2 * np.pi) - np.pi)
    return int(equal[np.argmin(distance)])


def _group_candidates(members: list[int], frames: list[dict | None], owner: np.ndarray, data: dict,
                      size: tuple[float, float], kw: float, cfg: Config) -> list[dict]:
    """Every panel that fits on one field (see `segment_groups`), with its energy."""
    rows, cols = np.nonzero(np.isin(owner, members))
    if rows.size == 0:
        return []
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1   # work on the field's window
    mine = owner[r0:r1, c0:c1]
    free = _majority(np.isin(mine, members))
    if not free.any():
        return []
    flux, res = data["flux"][r0:r1, c0:c1], data["res"]
    t = data["transform"]
    t = Affine(t.a, t.b, t.c + t.a * c0 + t.b * r0, t.d, t.e, t.f + t.d * c0 + t.e * r0)   # the window's own
    frame = frames[max(members, key=lambda i: int((mine == i).sum()))]   # the largest segment sets the direction
    pitch, azimuth = frame["pitch"], frame["azimuth"]
    if pitch < FLAT_PITCH_DEG:                       # flat: no eaves to follow, and nothing is foreshortened
        x, y = _centres(t, *np.nonzero(free))
        pitch, azimuth = 0.0, _outline_azimuth(x, y)
    cos = math.cos(math.radians(pitch))
    grid = _local_grid(free, flux, t, res, frame["x"], frame["y"], azimuth, pad=cfg.layout_margin_m + 3 * res)
    height_m, width_m = size
    best: tuple[float, list[dict]] = (0.0, [])
    for portrait in (False, True):
        du = width_m if portrait else height_m               # along the eaves
        dv = (height_m if portrait else width_m) * cos       # down the slope, seen from above
        total, found = _best_lattice(grid, res, du, dv, cfg.layout_gap_m, cfg.layout_gap_m * cos,
                                     cfg.layout_margin_m, cfg.layout_margin_m * cos, kw, cfg.layout_shift_rows)
        if total <= best[0]:
            continue
        eu, ed, inv = grid["eu"], grid["ed"], ~t
        panels = []
        for u, v, row, energy in found:
            cu, cv = grid["u0"] + u + du / 2, grid["v0"] + v + dv / 2
            x, y = frame["x"] + cu * eu[0] + cv * ed[0], frame["y"] + cu * eu[1] + cv * ed[1]
            c, r = (int(math.floor(k)) for k in _apply(inv, x, y))
            segment = int(mine[r, c]) if 0 <= r < mine.shape[0] and 0 <= c < mine.shape[1] else -1
            panels.append({"segment": segment if segment in members else members[0], "group": members[0],
                           "portrait": portrait, "azimuth": azimuth, "row": row, "u": u, "du": du,
                           "energy": energy, "x": x, "y": y})
        best = (total, panels)
    return best[1]


# ------------------------------------------------------------------ 5. order
def _neighbours(cands: list[dict], gap: float) -> list[list[int]]:
    """Panels that touch: next to each other in a row, or overlapping in the next row."""
    out: list[list[int]] = [[] for _ in cands]
    rows: dict[tuple[int, int], list[int]] = {}
    for i, c in enumerate(cands):
        rows.setdefault((c["group"], c["row"]), []).append(i)
    for (group, row), members in rows.items():
        members.sort(key=lambda i: cands[i]["u"])
        for i, j in zip(members, members[1:]):
            if cands[j]["u"] - cands[i]["u"] < 1.25 * (cands[i]["du"] + gap):
                out[i].append(j)
                out[j].append(i)
        for i in members:
            for j in rows.get((group, row + 1), []):
                if abs(cands[i]["u"] - cands[j]["u"]) < 0.75 * cands[i]["du"]:
                    out[i].append(j)
                    out[j].append(i)
    return out


def _order(cands: list[dict], compactness: float, gap: float) -> list[int]:
    """Indices of `cands`, best first: the next panel is the one with the highest energy x (1 +
    compactness x number of chosen panels it touches), so arrays grow around the best spot.
    Among equals the one closest to the panels already chosen wins (at first: closest to the
    middle of the best panels), so an evenly lit roof fills as one compact block."""
    near = _neighbours(cands, gap)
    xy = np.array([(c["x"], c["y"]) for c in cands])
    energy = np.array([c["energy"] for c in cands])
    centre = xy[energy >= energy.max() * (1 - 1e-3)].mean(axis=0)
    heap = [(-float(energy[i]), float(((xy[i] - centre) ** 2).sum()), i, 0) for i in range(len(cands))]
    heapq.heapify(heap)
    touching, chosen, out, total = [0] * len(cands), [False] * len(cands), [], np.zeros(2)
    while heap:
        _, _, i, seen = heapq.heappop(heap)
        if chosen[i] or seen != touching[i]:          # taken, or an older score of this panel
            continue
        chosen[i] = True
        out.append(i)
        total += xy[i]
        centre = total / len(out)
        for j in near[i]:
            if not chosen[j]:
                touching[j] += 1
                heapq.heappush(heap, (-float(energy[j]) * (1 + compactness * touching[j]),
                                      float(((xy[j] - centre) ** 2).sum()), j, touching[j]))
    return out


def _order_pro(cands: list[dict]) -> list[int]:
    """Alternative professional layout ordering:
    Groups panels by segment and grid coordinates, prioritizing the sunniest
    facet and expanding as clean, aesthetic rectangular arrays (tables) without
    irregular jagged teeth or isolated orphan panels.
    """
    if not cands:
        return []

    by_seg = defaultdict(list)
    for idx, c in enumerate(cands):
        by_seg[c["segment"]].append(idx)

    # Sort segments by maximum solar yield
    seg_rank = sorted(by_seg.keys(), key=lambda s: max(cands[i]["energy"] for i in by_seg[s]), reverse=True)

    final_order = []

    for seg in seg_rank:
        indices = by_seg[seg]
        if not indices:
            continue

        seg_cands = [cands[i] for i in indices]
        all_u = sorted(list(set(round(c["u"], 2) for c in seg_cands)))
        u_to_col = {u: col for col, u in enumerate(all_u)}

        grid_panels = {}
        for i_local, c in enumerate(seg_cands):
            col = u_to_col[round(c["u"], 2)]
            row = c["row"]
            grid_panels[(row, col)] = (indices[i_local], c["energy"])

        available = set(grid_panels.keys())

        # Select the best seed: highest local 3x3 density and energy
        best_seed = None
        best_seed_score = -1e9
        for (r, c) in available:
            neighbors = [(r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (r + dr, c + dc) in available]
            density = len(neighbors)
            avg_e = sum(grid_panels[nb][1] for nb in neighbors) / density
            score = avg_e * 2.0 + density * 50.0
            if score > best_seed_score:
                best_seed_score = score
                best_seed = (r, c)

        chosen_coords = []
        chosen_set = set()

        chosen_coords.append(best_seed)
        chosen_set.add(best_seed)
        available.remove(best_seed)

        while available:
            best_next = None
            best_next_score = -1e9

            # Current bounding box
            curr_rows = [rc[0] for rc in chosen_set]
            curr_cols = [rc[1] for rc in chosen_set]
            r_min, r_max = min(curr_rows), max(curr_rows)
            c_min, c_max = min(curr_cols), max(curr_cols)
            curr_perimeter = 2 * ((r_max - r_min + 1) + (c_max - c_min + 1))

            for (r, c) in available:
                cardinal_neighbors = 0
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    if (r + dr, c + dc) in chosen_set:
                        cardinal_neighbors += 1

                diag_neighbors = 0
                for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                    if (r + dr, c + dc) in chosen_set:
                        diag_neighbors += 1

                if cardinal_neighbors == 0 and diag_neighbors == 0:
                    continue  # must touch existing cluster

                energy = grid_panels[(r, c)][1]

                new_r_min = min(r_min, r)
                new_r_max = max(r_max, r)
                new_c_min = min(c_min, c)
                new_c_max = max(c_max, c)
                new_perimeter = 2 * ((new_r_max - new_r_min + 1) + (new_c_max - new_c_min + 1))
                delta_perimeter = new_perimeter - curr_perimeter

                inside_box = (r_min <= r <= r_max) and (c_min <= c <= c_max)

                score = (cardinal_neighbors * 40.0 +
                         diag_neighbors * 15.0 +
                         (60.0 if inside_box else 0.0) -
                         delta_perimeter * 25.0 +
                         energy / 10.0)

                if score > best_next_score:
                    best_next_score = score
                    best_next = (r, c)

            if best_next is None:
                # Disjoint island: pick the panel with highest energy
                best_next = max(available, key=lambda rc: grid_panels[rc][1])

            chosen_coords.append(best_next)
            chosen_set.add(best_next)
            available.remove(best_next)

        final_order.extend([grid_panels[rc][0] for rc in chosen_coords])

    return final_order

