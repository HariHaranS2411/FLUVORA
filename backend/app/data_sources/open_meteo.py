"""Clients for the Open-Meteo APIs verified live on 2026-09-24
(see DATA_SOURCE_MATRIX.md). Real endpoints, real parameters, no key needed.
Batching is supported by the API via comma-separated coordinates."""
from __future__ import annotations

import asyncio
from datetime import date

import httpx

from ..config import settings

ARCHIVE_DAILY_VARS = [
    "precipitation_sum",
    "soil_moisture_0_to_7cm_mean",
    "soil_moisture_3_to_9cm_mean",
    "et0_fao_evapotranspiration",
    "precipitation_hours",
    "wind_speed_10m_max",
]

FORECAST_HOURLY_VARS = [
    "precipitation",
    "soil_moisture_0_to_7cm",
]

CURRENT_VARS = ["precipitation", "rain", "soil_moisture_0_to_7cm"]


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict | list:
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"open-meteo error: {data.get('reason')}")
    return data


def _chunks(coords: list[tuple[float, float]]):
    step = max(1, settings.fetch_batch_size)
    for i in range(0, len(coords), step):
        yield coords[i:i + step]


async def fetch_archive_daily(
    coords: list[tuple[float, float]], start: date, end: date
) -> list[dict]:
    """Daily ERA5 history per coordinate. Returns list aligned with input order."""
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        for chunk in _chunks(coords):
            params = {
                "latitude": ",".join(f"{c[0]:.4f}" for c in chunk),
                "longitude": ",".join(f"{c[1]:.4f}" for c in chunk),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": ",".join(ARCHIVE_DAILY_VARS),
                "timezone": "Asia/Kolkata",
            }
            data = await _get_json(client, settings.open_meteo_archive_url, params)
            if isinstance(data, dict):
                data = [data]
            out.extend(data)
            await asyncio.sleep(settings.fetch_pause_seconds)
    return out


async def fetch_current(coords: list[tuple[float, float]]) -> list[dict]:
    """Current conditions per coordinate (real 'current' block with timestamp).
    Rate-limited chunks are skipped (returned as {}) so a refresh cycle still
    covers the districts it can; the scheduler retries on the next cycle."""
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        for chunk in _chunks(coords):
            params = {
                "latitude": ",".join(f"{c[0]:.4f}" for c in chunk),
                "longitude": ",".join(f"{c[1]:.4f}" for c in chunk),
                "current": ",".join(CURRENT_VARS),
                "hourly": ",".join(FORECAST_HOURLY_VARS),
                "past_hours": 72,
                "forecast_hours": 24,
                "timezone": "Asia/Kolkata",
            }
            try:
                data = await _get_json(client, settings.open_meteo_forecast_url, params)
            except httpx.HTTPStatusError as e:
                # 429/5xx/etc: skip this chunk this cycle; scheduler retries later
                out.extend([{} for _ in chunk])
                print(f"  forecast chunk skipped (HTTP {e.response.status_code})")
                await asyncio.sleep(settings.fetch_pause_seconds)
                continue
            except httpx.HTTPError as e:
                out.extend([{} for _ in chunk])
                print(f"  forecast chunk skipped ({type(e).__name__})")
                await asyncio.sleep(settings.fetch_pause_seconds)
                continue
            if isinstance(data, dict):
                data = [data]
            out.extend(data)
            await asyncio.sleep(settings.fetch_pause_seconds)
    return out


async def fetch_river_discharge(
    coords: list[tuple[float, float]], start: date, end: date
) -> list[dict]:
    """GloFAS v4 daily river discharge (m3/s) from the Flood API."""
    out: list[dict] = []
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        for chunk in _chunks(coords):
            params = {
                "latitude": ",".join(f"{c[0]:.4f}" for c in chunk),
                "longitude": ",".join(f"{c[1]:.4f}" for c in chunk),
                "daily": "river_discharge",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
            data = await _get_json(client, settings.open_meteo_flood_url, params)
            if isinstance(data, dict):
                data = [data]
            out.extend(data)
            await asyncio.sleep(settings.fetch_pause_seconds)
    return out


async def fetch_elevation(coords: list[tuple[float, float]]) -> list[float]:
    """Copernicus DEM GLO-90 elevation (m). One value per coordinate, input order."""
    out: list[float] = []
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        for chunk in _chunks(coords):
            params = {
                "latitude": ",".join(f"{c[0]:.4f}" for c in chunk),
                "longitude": ",".join(f"{c[1]:.4f}" for c in chunk),
            }
            data = await _get_json(client, settings.open_meteo_elevation_url, params)
            if not isinstance(data, dict) or "elevation" not in data:
                raise RuntimeError("unexpected elevation response")
            out.extend(float(v) for v in data["elevation"])
            await asyncio.sleep(0.2)
    return out
