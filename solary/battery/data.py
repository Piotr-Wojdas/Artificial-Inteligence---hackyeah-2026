"""Inputs of the battery controller on one 15-minute grid (UTC, each step labelled by its start).

  * Prices: RCE (rynkowa cena energii) from PSE, zł/kWh netto, every 15 minutes (hourly values
    before October 2025 come repeated in each quarter). Tomorrow's prices appear around 14:00.
  * Weather history: NASA POWER hourly irradiance (global, diffuse) and air temperature.
  * Weather forecast: Open-Meteo, hourly, a few days ahead.
  * PV: irradiance transposed onto each roof plane (isotropic sky), temperature loss, system loss.
  * Consumption: a typical Polish household (synthetic, G11 shape) scaled to the yearly total.

Everything downloaded is cached in cfg.data_dir / "battery".
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ..config import CONFIG, Config
from ..errors import SolaryError

PSE_URL = "https://api.raporty.pse.pl/api/rce-pln"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/hourly/point"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TZ = "Europe/Warsaw"
STEP_H = 0.25                      # hours per step
STEPS_PER_DAY = 96
NOCT_C = 45.0                      # cell temperature model: nominal operating cell temperature
TEMP_COEFF = -0.004                # power change per deg C of cell temperature above 25 C
ALBEDO = 0.2


class BatteryDataError(SolaryError):
    pass


def cache_dir(cfg: Config = CONFIG) -> Path:
    path = cfg.data_dir / "battery"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_json(url: str, params: dict | None = None, timeout: float = 60, attempts: int = 3) -> dict:
    for attempt in range(attempts):              # free APIs drop a connection now and then
        try:
            r = requests.get(url, params=params, timeout=timeout,
                             headers={"User-Agent": "solari-battery/0.1 (HackYeah 2026)"})
            break
        except requests.RequestException as e:
            if attempt == attempts - 1:
                raise BatteryDataError(f"{url.split('/')[2]} unreachable: {type(e).__name__}") from None
            time.sleep(2 ** attempt)
    if not r.ok:
        raise BatteryDataError(f"{url.split('/')[2]} error {r.status_code}: {r.text[:200]}")
    return r.json()


def quarters(start: datetime, end: datetime) -> pd.DatetimeIndex:
    """15-minute steps from `start` (inclusive) to `end` (exclusive), UTC."""
    return pd.date_range(pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC"),
                         freq="15min", inclusive="left").as_unit("ns")


def local_day_bounds(first: date, last: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """UTC instants of local midnight starting `first` and local midnight after `last`."""
    return (pd.Timestamp(first).tz_localize(TZ).tz_convert("UTC"),
            pd.Timestamp(last + timedelta(days=1)).tz_localize(TZ).tz_convert("UTC"))


# ------------------------------------------------------------------ prices
def rce(first: date, last: date, cfg: Config = CONFIG) -> pd.Series:
    """RCE in zł/kWh for the local days `first`..`last`, one value per quarter (UTC index).
    Quarters PSE has not published yet are missing from the series."""
    parts, month = [], date(first.year, first.month, 1)
    while month <= last:
        parts.append(_rce_month(month, cfg))
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    s = pd.concat(parts).sort_index()
    s = s[~s.index.duplicated()]
    lo, hi = local_day_bounds(first, last)
    return s[(s.index >= lo) & (s.index < hi)]


def _rce_month(month: date, cfg: Config) -> pd.Series:
    nxt = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    path = cache_dir(cfg) / f"rce_{month:%Y-%m}.json"
    finished = nxt + timedelta(days=1) < date.today()          # nothing more will be published
    if path.exists() and (finished or time.time() - path.stat().st_mtime < 3600):
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        flt = f"business_date ge '{month:%Y-%m-%d}' and business_date le '{nxt - timedelta(days=1):%Y-%m-%d}'"
        rows, url, params = [], PSE_URL, {"$filter": flt}
        try:
            while url:
                page = _get_json(url, params)
                rows += [[r["dtime_utc"], r["rce_pln"]] for r in page.get("value", [])]
                url, params = page.get("nextLink"), None
        except BatteryDataError:
            if not path.exists():
                raise
            rows = json.loads(path.read_text(encoding="utf-8"))  # stale prices beat none
        else:
            path.write_text(json.dumps(rows), encoding="utf-8")
    if not rows:
        return pd.Series(dtype=float)
    # dtime_utc is the END of the quarter; prices are zł/MWh
    idx = pd.to_datetime([r[0] for r in rows], utc=True).as_unit("ns") - pd.Timedelta(minutes=15)
    return pd.Series([float(r[1]) / 1000 for r in rows], index=idx, name="rce")


# ------------------------------------------------------------------ weather
def weather_history(lat: float, lon: float, first: date, last: date, cfg: Config = CONFIG) -> pd.DataFrame:
    """Hourly NASA POWER weather (index: UTC start of the hour): ghi, dhi in W/m2, temp in C."""
    frames = []
    for year in range(first.year, last.year + 1):
        path = cache_dir(cfg) / f"nasa_{lat:.2f}_{lon:.2f}_{year}.json"
        complete = date(year, 12, 31) < date.today() - timedelta(days=120)
        if path.exists() and (complete or time.time() - path.stat().st_mtime < 7 * 86400):
            p = json.loads(path.read_text(encoding="utf-8"))
        else:
            p = _get_json(NASA_URL, {"parameters": "ALLSKY_SFC_SW_DWN,ALLSKY_SFC_SW_DIFF,T2M", "community": "RE",
                                     "latitude": round(lat, 2), "longitude": round(lon, 2),
                                     "start": f"{year}0101", "end": f"{year}1231", "format": "JSON",
                                     "time-standard": "UTC"}, timeout=180)["properties"]["parameter"]
            path.write_text(json.dumps(p), encoding="utf-8")
        df = pd.DataFrame({"ghi": p["ALLSKY_SFC_SW_DWN"], "dhi": p["ALLSKY_SFC_SW_DIFF"], "temp": p["T2M"]})
        df.index = pd.to_datetime(df.index, format="%Y%m%d%H", utc=True).as_unit("ns")
        frames.append(df.replace(-999.0, np.nan))
    df = pd.concat(frames).sort_index()
    lo, hi = local_day_bounds(first, last)
    return df[(df.index >= lo - pd.Timedelta(hours=1)) & (df.index < hi + pd.Timedelta(hours=1))]


def weather_forecast(lat: float, lon: float, cfg: Config = CONFIG, days: int = 3) -> pd.DataFrame:
    """Hourly Open-Meteo forecast from yesterday to `days` ahead, same columns as the history."""
    path = cache_dir(cfg) / f"forecast_{lat:.2f}_{lon:.2f}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 3600:
        h = json.loads(path.read_text(encoding="utf-8"))
    else:
        h = _get_json(OPEN_METEO_URL, {"latitude": round(lat, 3), "longitude": round(lon, 3), "timezone": "UTC",
                                       "hourly": "shortwave_radiation,diffuse_radiation,temperature_2m",
                                       "past_days": 1, "forecast_days": days})["hourly"]
        path.write_text(json.dumps(h), encoding="utf-8")
    df = pd.DataFrame({"ghi": h["shortwave_radiation"], "dhi": h["diffuse_radiation"], "temp": h["temperature_2m"]},
                      index=pd.to_datetime(h["time"], utc=True).as_unit("ns"))
    # Open-Meteo radiation is the mean of the PRECEDING hour: label it by the hour's start instead
    df[["ghi", "dhi"]] = df[["ghi", "dhi"]].shift(-1)
    return df.astype(float)


# ------------------------------------------------------------------ PV
def solar_position(times: pd.DatetimeIndex, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    """Sun zenith and compass azimuth in degrees (NOAA formulas, accurate to a fraction of a degree)."""
    t = times.tz_convert("UTC")
    doy = t.dayofyear.to_numpy()
    hours = (t.hour + t.minute / 60 + t.second / 3600).to_numpy()
    g = 2 * np.pi / 365 * (doy - 1 + (hours - 12) / 24)
    eot = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g) - 0.014615 * np.cos(2 * g)
                    - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    hour_angle = np.radians((hours * 60 + eot + 4 * lon) / 4 - 180)
    phi = math.radians(lat)
    cos_z = np.clip(np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(hour_angle), -1, 1)
    zenith = np.arccos(cos_z)
    sin_z = np.maximum(np.sin(zenith), 1e-9)
    cos_az = np.clip((np.sin(decl) - np.sin(phi) * cos_z) / (np.cos(phi) * sin_z), -1, 1)
    azimuth = np.degrees(np.arccos(cos_az))
    azimuth = np.where(hour_angle > 0, 360 - azimuth, azimuth)
    return np.degrees(zenith), azimuth


def to_quarters(hourly: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Hourly means (labelled by the hour's start) interpolated to the middle of each quarter."""
    centres = hourly.index + pd.Timedelta(minutes=30)
    x = (centres - index[0]).total_seconds().to_numpy()
    q = (index + pd.Timedelta(minutes=7.5) - index[0]).total_seconds().to_numpy()
    out = {}
    for col in hourly.columns:
        v = hourly[col].to_numpy(float)
        ok = np.isfinite(v)
        out[col] = np.interp(q, x[ok], v[ok]) if ok.any() else np.full(len(q), np.nan)
    return pd.DataFrame(out, index=index)


def pv_energy(weather: pd.DataFrame, lat: float, lon: float, planes: list[tuple[float, float, float]],
              loss_pct: float = 14.0) -> np.ndarray:
    """kWh produced in each quarter of `weather` (quarter index, ghi/dhi/temp at quarter middles)
    by `planes` = [(kWp, tilt deg, compass azimuth deg), ...]."""
    middle = weather.index + pd.Timedelta(minutes=7.5)
    zenith, sun_az = solar_position(middle, lat, lon)
    ghi = np.clip(weather["ghi"].to_numpy(float), 0, None)
    dhi = np.minimum(np.clip(weather["dhi"].to_numpy(float), 0, None), ghi)
    cos_z = np.cos(np.radians(zenith))
    up = zenith < 89.0
    dni = np.where(cos_z > 0.087, (ghi - dhi) / np.maximum(cos_z, 0.087), 0.0)   # beam, capped near the horizon
    temp = weather["temp"].to_numpy(float) if "temp" in weather else np.full(len(ghi), 15.0)
    out = np.zeros(len(ghi))
    for kwp, tilt, azimuth in planes:
        b, a = math.radians(tilt), math.radians(azimuth)
        cos_aoi = (cos_z * math.cos(b) + np.sin(np.radians(zenith)) * math.sin(b)
                   * np.cos(np.radians(sun_az) - a))
        poa = (dni * np.clip(cos_aoi, 0, None) + dhi * (1 + math.cos(b)) / 2
               + ghi * ALBEDO * (1 - math.cos(b)) / 2) * up
        cell = temp + poa / 800 * (NOCT_C - 20)
        out += kwp * poa / 1000 * (1 + TEMP_COEFF * (cell - 25))
    return np.nan_to_num(out) * (1 - loss_pct / 100) * STEP_H


# ------------------------------------------------------------------ consumption
# kW of a typical household (no electric heating) for each local hour, scaled later to the yearly total
_WEEKDAY = np.array([.22, .19, .18, .18, .19, .25, .42, .55, .45, .36, .33, .33,
                     .35, .34, .33, .37, .48, .66, .82, .88, .84, .70, .50, .32])
_WEEKEND = np.array([.25, .21, .19, .18, .18, .20, .26, .37, .50, .55, .55, .56,
                     .58, .53, .47, .46, .52, .66, .80, .86, .82, .70, .52, .35])


def load_profile(index: pd.DatetimeIndex, annual_kwh: float, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """(actual, expected) kWh per quarter of a typical household using about `annual_kwh` a year.
    The expected profile is what a forecast knows (time of day, weekday, season); the actual one
    adds day-to-day and minute-to-minute randomness (kettle, oven, washing machine)."""
    local = index.tz_convert(TZ)
    h = (local.hour + local.minute / 60 + 7.5 / 60).to_numpy()
    weekend = np.asarray(local.dayofweek) >= 5
    shape = np.where(weekend, np.interp(h, np.arange(24) + 0.5, _WEEKEND, period=24),
                     np.interp(h, np.arange(24) + 0.5, _WEEKDAY, period=24))
    season = 1 + 0.22 * np.cos(2 * np.pi * (local.dayofyear.to_numpy() - 15) / 365.25)   # more in winter
    awake = (h > 6) & (h < 23)
    spike_p, spike_kwh = 0.02, 0.4                      # a 1-2.5 kW appliance for a quarter, now and then
    spikes_year = spike_p * spike_kwh * STEPS_PER_DAY * 17 / 24 * 365.25
    mean_kw = (5 * _WEEKDAY.mean() + 2 * _WEEKEND.mean()) / 7
    base = shape * season * STEP_H * max(annual_kwh - spikes_year, 0.2 * annual_kwh) / (mean_kw * 24 * 365.25)
    expected = base + spike_p * spike_kwh * awake
    rng = np.random.default_rng(seed)
    days = local.normalize().as_unit("ns").asi8 // (86400 * 10**9)
    daily = np.exp(rng.normal(-0.011, 0.15, days.max() - days.min() + 1))[days - days.min()]
    noise = np.exp(rng.normal(-0.061, 0.35, len(index)))
    spikes = (rng.random(len(index)) < spike_p) * rng.uniform(0.2, 0.6, len(index)) * awake
    return base * daily * noise + spikes, expected


# ------------------------------------------------------------------ everything for one place
@dataclass
class Inputs:
    """One place on one 15-minute grid: prices, PV per plane orientation and consumption shape."""
    index: pd.DatetimeIndex
    rce: np.ndarray                   # zł/kWh netto (NaN where unknown)
    weather: pd.DataFrame             # ghi, dhi, temp per quarter
    lat: float
    lon: float

    def pv(self, planes: list[tuple[float, float, float]], loss_pct: float = 14.0) -> np.ndarray:
        return pv_energy(self.weather, self.lat, self.lon, planes, loss_pct)


def history(lat: float, lon: float, first: date, last: date, cfg: Config = CONFIG) -> Inputs:
    """Past prices and weather for the local days first..last (prices and weather both complete)."""
    lo, hi = local_day_bounds(first, last)
    index = quarters(lo, hi)
    prices = rce(first, last, cfg).reindex(index)
    weather = to_quarters(weather_history(lat, lon, first, last, cfg), index)
    return Inputs(index, prices.to_numpy(float), weather, lat, lon)


def live(lat: float, lon: float, cfg: Config = CONFIG, days: int = 2) -> Inputs:
    """From the start of today (local) for `days` days: published prices (NaN after the last
    published quarter) and the weather forecast."""
    today = pd.Timestamp.now(tz=TZ).date()
    lo, hi = local_day_bounds(today - timedelta(days=1), today + timedelta(days=days - 1))
    index = quarters(lo, hi)
    prices = rce(today - timedelta(days=1), today + timedelta(days=1), cfg).reindex(index)
    weather = to_quarters(weather_forecast(lat, lon, cfg, days=days + 1), index)
    return Inputs(index, prices.to_numpy(float), weather, lat, lon)
