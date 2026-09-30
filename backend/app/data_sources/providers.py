"""Source abstraction layer (spec: provider interfaces).

The concrete implementations are the existing verified clients in
`open_meteo.py` (ERA5 archive, current/forecast, GloFAS flood, elevation) and
`ingest_events.py` (GDACS). This module defines the seams as typing Protocols
so a future authoritative source (e.g. IMD/CWC if they publish APIs, or
data.gov.in datasets already supported via `datagov.py`) can be added without
touching the risk engine, ingestion or UI code.

Nothing here fetches or fabricates data — it only documents the contracts the
existing real providers already satisfy.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

Coord = tuple[float, float]


@runtime_checkable
class RainfallProvider(Protocol):
    """Daily historical rainfall + current rainfall (mm)."""

    async def fetch_archive_daily(self, coords: list[Coord], start: date, end: date) -> list[dict]:
        """Per-coordinate daily precipitation_sum (+ soil vars as available)."""
        ...


@runtime_checkable
class SoilMoistureProvider(Protocol):
    """Volumetric soil water content (m³/m³)."""

    async def fetch_archive_daily(self, coords: list[Coord], start: date, end: date) -> list[dict]:
        """Per-coordinate daily soil_moisture_0_to_7cm_mean etc."""
        ...


@runtime_checkable
class DischargeProvider(Protocol):
    """Historical + current river discharge (m³/s)."""

    async def fetch_river_discharge(
        self, coords: list[Coord], start: date, end: date
    ) -> list[dict]:
        """Per-coordinate daily river_discharge."""
        ...


@runtime_checkable
class ForecastProvider(Protocol):
    """Short-term forecast precipitation/soil (NOT yet used by the model)."""

    async def fetch_current(self, coords: list[Coord]) -> list[dict]:
        """Per-coordinate current block + hourly past/forecast series."""
        ...


@runtime_checkable
class FloodEventProvider(Protocol):
    """Officially reported flood/cyclone events with stable external IDs."""

    async def fetch_events(self, start_year: int, end_year: int):
        """DataFrame with event_id, name, from_date, to_date, lat, lon, source."""
        ...


@runtime_checkable
class BoundaryProvider(Protocol):
    """Administrative district polygons (ADM2) with stable shape IDs."""

    def load_geometry(self) -> dict:
        """GeoJSON FeatureCollection keyed by shapeID."""
        ...


# Concrete implementations (imported lazily to avoid cycles):
#   RainfallProvider/SoilMoistureProvider/DischargeProvider/ForecastProvider
#       -> app.data_sources.open_meteo (fetch_archive_daily, fetch_current,
#          fetch_river_discharge)
#   FloodEventProvider -> ml.data_processing.ingest_events.fetch_events (GDACS)
#   BoundaryProvider -> data/raw/geoBoundaries-IND-ADM2_simplified.geojson via
#       prepare_districts / locator._load_geometry
#
# data.gov.in (datagov.py) is a second FloodEventProvider path, gated on
# DATA_GOV_IN_API_KEY. NASA POWER / ReliefWeb are documented backups in
# DATA_SOURCE_MATRIX.md, not integrated — never simulated.
