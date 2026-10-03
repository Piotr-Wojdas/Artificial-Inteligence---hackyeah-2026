"""Every assumption of the module in one place (also returned by the API under "assumptions")."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .env import load_env

load_env()  # before the defaults below read the environment


@dataclass(frozen=True)
class Config:
    # --- storage ---------------------------------------------------------------------------
    # Cache folder. Google Solar data inside it is deleted after `cache_days` (Solar API terms).
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("SOLARY_DATA", "data")))
    cache_days: int = 30

    # --- address search (OpenStreetMap Nominatim) --------------------------------------------
    country_codes: str | None = "pl"   # limit the search to Poland; None = whole world
    language: str = "pl"               # language of the returned address label
    # Nominatim place_rank: 26-27 = street, 28-30 = house number / building / POI
    min_house_rank: int = 28

    # --- roof (Google Solar API) -------------------------------------------------------------
    qualities: tuple[str, ...] = ("HIGH", "MEDIUM")  # tried in this order
    warn_distance_m: float = 10.0      # building further than this from the address -> warning
    # Which panels are used for a given size:
    #   "google" - the order of Google's layout algorithm: the first N panels are the layout
    #              Google proposes for N panels (compact arrays, close to best-first);
    #   "yield"  - strictly the highest yearly yield first (0-1 % more energy, scattered panels).
    panel_order: str = "google"
    # None = the panel Google laid out (400 W, 1.879 x 1.045 m). A different wattage scales
    # power and energy linearly; valid only for panels of about the same size (~1.95 m2).
    panel_watts: float | None = None

    # --- production estimate -----------------------------------------------------------------
    # DC -> AC: inverter, cables, soiling, mismatch. 14 % is the PVGIS default.
    system_loss_pct: float = 14.0
    pvgis_mounting: str = "building"   # PVGIS: "building" = roof-mounted (warmer), "free" = open rack
    default_kwp: float = 6.0           # size shown when the caller does not choose one
    sizes_kwp: tuple[float, ...] = (3, 5, 6, 8, 10, 15, 20, 30, 50)  # comparison table
    # Estimate without roof data (address outside Google's coverage): assumed orientation.
    generic_tilt_deg: float = 35.0     # close to the yearly optimum in Poland
    generic_azimuth_deg: float = 180.0  # compass azimuth: 180 = south

    @property
    def roof_dir(self) -> Path:
        return self.data_dir / "roof"

    @property
    def ac_factor(self) -> float:
        return 1.0 - self.system_loss_pct / 100.0

    def public(self) -> dict:
        """The assumptions worth showing next to the numbers."""
        return {
            "system_loss_pct": self.system_loss_pct,
            "panel_watts_override": self.panel_watts,
            "panel_order": self.panel_order,
            "pvgis_mounting": self.pvgis_mounting,
            "search_country_codes": self.country_codes,
            "cache_days": self.cache_days,
            "generic_tilt_deg": self.generic_tilt_deg,
            "generic_azimuth_deg": self.generic_azimuth_deg,
        }


CONFIG = Config()
