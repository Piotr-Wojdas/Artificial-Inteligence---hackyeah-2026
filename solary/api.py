"""HTTP API for a frontend, plus the demo page.

    uv run python -m solary.api            # http://127.0.0.1:8000  (demo page at /, docs at /docs)

GET /api/health                     {"ok": true, "google_key": true|false}
GET /api/roof?address=...&kwp=6     roof, panel layout and production (see README for the fields)
GET /api/roof?lat=..&lon=..&panels=15
GET /api/roof?address=...&layout=own&margin=0.2   panels placed by our algorithm instead of Google's
GET /api/roof/image/{name}          PNG previews named in the "images" field
GET /api/battery?lat=..&lon=..&planes=6:35:180&battery_kwh=10&annual_kwh=4000&tariff=g11
                                    battery: yearly savings and the plan for today and tomorrow

Set SOLARY_CORS_ORIGINS (comma-separated) when the frontend runs on another origin.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import CONFIG
from .errors import SolaryError
from .geocode import AddressNotFound, GeocodingError
from .service import analyze
from .solar_api import MissingApiKey, SolarApiError, has_api_key, purge_expired

IMAGE_NAME = re.compile(r"^(confirm|panels)_[0-9A-Za-z._-]+\.png$")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="solary", version="0.1.0",
              description="Panel layout on a real roof found by address, with a production estimate.")
_origins = [o.strip() for o in os.environ.get("SOLARY_CORS_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET"], allow_headers=["*"])
_lock = threading.Lock()  # one analysis at a time: the file caches are not written concurrently
purge_expired(CONFIG)


@app.get("/api/health")
def health():
    return {"ok": True, "google_key": has_api_key()}


@app.get("/api/roof")
def roof(address: str | None = Query(None, max_length=300),
         lat: float | None = Query(None, ge=-90, le=90), lon: float | None = Query(None, ge=-180, le=180),
         kwp: float | None = Query(None, gt=0, le=1000), panels: int | None = Query(None, ge=1, le=100000),
         tilt: float | None = Query(None, ge=0, le=90), azimuth: float | None = Query(None, ge=0, le=360),
         images: bool = True, layout: str | None = Query(None, pattern="^(google|own|pro)$"),
         margin: float | None = Query(None, ge=0, le=2),
         retail_price: float | None = Query(None, ge=0.1, le=10.0),
         feed_in_price: float | None = Query(None, ge=0.0, le=10.0),
         self_consumption: float | None = Query(None, ge=0.0, le=100.0)):
    """Roof at `address` (or lat/lon) with the best `panels` panels (or those closest to `kwp`).
    `layout=own` or `layout=pro` places the panels with our algorithms, keeping `margin` metres free around each."""
    if not (address and address.strip()) and (lat is None or lon is None):
        raise HTTPException(400, "give an address, or both lat and lon")
    changes = {k: v for k, v in {"layout": layout, "layout_margin_m": margin}.items() if v is not None}
    how = {"cfg": dataclasses.replace(CONFIG, **changes)} if changes else {}
    extra = {}
    if retail_price is not None:
        extra["retail_price"] = retail_price
    if feed_in_price is not None:
        extra["feed_in_price"] = feed_in_price
    if self_consumption is not None:
        extra["self_consumption"] = self_consumption
    try:
        with _lock:
            res = analyze(address=address, lat=lat, lon=lon, kwp=kwp, panels=panels, images=images,
                          tilt=tilt, azimuth=azimuth, **how, **extra)
    except AddressNotFound as e:
        raise HTTPException(404, str(e)) from e
    except MissingApiKey as e:
        raise HTTPException(503, str(e)) from e
    except (GeocodingError, SolarApiError) as e:
        raise HTTPException(502, str(e)) from e
    except SolaryError as e:
        raise HTTPException(400, str(e)) from e
    res["images"] = {k: f"/api/roof/image/{name}" for k, name in res["images"].items()}
    return res


def parse_planes(text: str) -> list[tuple[float, float, float]]:
    """ "kWp:tilt:azimuth,..." -> [(kWp, tilt, azimuth), ...]"""
    planes = []
    for part in text.split(","):
        kwp, tilt, azimuth = (float(v) for v in part.split(":"))
        if not (0 < kwp <= 1000 and 0 <= tilt <= 90 and 0 <= azimuth <= 360):
            raise ValueError(part)
        planes.append((kwp, tilt, azimuth))
    return planes


@app.get("/api/battery")
def battery(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
            planes: str = Query("6:35:180", max_length=400, pattern=r"^[0-9.:,]+$"),
            kwh_year: float | None = Query(None, gt=0, le=1_000_000),
            battery_kwh: float = Query(10.0, gt=0, le=200), battery_kw: float | None = Query(None, gt=0, le=100),
            annual_kwh: float = Query(4000.0, gt=0, le=100_000), tariff: str = Query("g11", pattern="^(g11|dynamic)$"),
            soc: float = Query(0.5, ge=0, le=1), export_limit: float | None = Query(None, gt=0, le=100),
            plan: bool = True):
    """Battery for a house with PV `planes` ("kWp:tilt:azimuth,..."): bills over the test year
    without a battery, with the usual inverter, with the RL agent and at the optimum; and the
    agent's plan from now to the end of tomorrow. `kwh_year` rescales PV to the roof analysis;
    `export_limit` (kW) models a weak grid where the inverter trips above that export."""
    from .battery.data import BatteryDataError
    from .battery.model import Battery, Grid, Tariff
    from .battery.plan import battery_report

    try:
        pv = parse_planes(planes)
    except ValueError as e:
        raise HTTPException(400, "planes must be kWp:tilt:azimuth[,kWp:tilt:azimuth...]") from e
    store = Battery(capacity_kwh=battery_kwh, power_kw=battery_kw or battery_kwh / 2)
    try:
        with _lock:
            return battery_report(lat, lon, pv, annual_kwh, store, Tariff(kind=tariff), Grid(export_limit_kw=export_limit),
                                  soc, kwh_year, plan)
    except BatteryDataError as e:
        raise HTTPException(502, str(e)) from e
    except SolaryError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/roof/image/{name}")
def roof_image(name: str):
    path = CONFIG.roof_dir / name
    if not IMAGE_NAME.match(name) or not path.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/", include_in_schema=False)
def demo_page():
    page = WEB_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(404, "demo page not found (web/index.html)")
    return FileResponse(page, media_type="text/html")


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(prog="python -m solary.api", description="Start the solary HTTP API.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
