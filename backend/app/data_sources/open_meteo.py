"""Clients for the Open-Meteo APIs verified live on 2026-09-24
(see DATA_SOURCE_MATRIX.md). Real endpoints, real parameters, no key needed.
Batching is supported by the API via comma-separated coordinates."""
from __future__ import annotations

import asyncio
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..config import settings

ARCHIVE_DAILY_VARS = [
    # NB: soil_moisture_3_to_9cm_mean was dropped from the API's daily enum
    # (verified 2026-10-04: any request containing it returns 400). Nothing
    # downstream ever read it — features use the 0-7cm layer only.
    "precipitation_sum",
    "soil_moisture_0_to_7cm_mean",
    "et0_fao_evapotranspiration",
    "precipitation_hours",
    "wind_speed_10m_max",
]

FORECAST_HOURLY_VARS = [
    "precipitation",
    "soil_moisture_0_to_7cm",
]

CURRENT_VARS = ["precipitation", "rain", "soil_moisture_0_to_7cm"]

_CUSTOMER_HOSTS = {
    "api.open-meteo.com": "customer-api.open-meteo.com",
    "archive-api.open-meteo.com": "customer-archive-api.open-meteo.com",
    "flood-api.open-meteo.com": "customer-flood-api.open-meteo.com",
}
_CUSTOMER_HOST_NAMES = set(_CUSTOMER_HOSTS.values())


def prepare_open_meteo_request(url: str, params: dict) -> tuple[str, dict]:
    """Use Open-Meteo's reserved customer endpoint when a key is configured."""
    api_key = settings.open_meteo_api_key.strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    customer_host = _CUSTOMER_HOSTS.get(host)
    if not api_key or (customer_host is None and host not in _CUSTOMER_HOST_NAMES):
        return url, params

    if customer_host is not None:
        netloc = customer_host
        if parts.port:
            netloc += f":{parts.port}"
        url = urlunsplit(parts._replace(netloc=netloc))

    request_params = dict(params)
    request_params["apikey"] = api_key
    return url, request_params


async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict | list:
    url, params = prepare_open_meteo_request(url, params)
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"open-meteo error: {data.get('reason')}")
    return data


async def _get_json_retry(client: httpx.AsyncClient, url: str, params: dict,
                           deadline: float, what: str) -> dict | list:
    """_get_json with bounded backoff on throttle/transient errors (429/5xx),
    honouring Retry-After and a per-cycle time budget. Shared egress IPs (e.g.
    Render's) see a lot of aggregate traffic and get throttled in bursts; a few
    spaced retries turn a whole failed cycle into a slow-but-complete one.
    Raises once retries are exhausted or the budget is spent."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await _get_json(client, url, params)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            ra = e.response.headers.get("Retry-After", "")
            wait = min(int(ra) if ra.isdigit() else 15 * attempt, 60)
            retryable = code in (429, 500, 502, 503, 504)
            if (not retryable or attempt >= 3 or
                    asyncio.get_running_loop().time() + wait > deadline):
                raise
            print(f"  {what} throttled (HTTP {code}), retry {attempt}/3 in {wait}s")
            await asyncio.sleep(wait)
        except httpx.HTTPError as e:
            if (attempt >= 3 or
                    asyncio.get_running_loop().time() + 15 * attempt > deadline):
                raise
            print(f"  {what} failed ({type(e).__name__}), retry {attempt}/3 in 15s")
            await asyncio.sleep(15 * attempt)


def _chunks(coords: list[tuple[float, float]]):
    step = max(1, settings.fetch_batch_size)
    for i in range(0, len(coords), step):
        yield coords[i:i + step]


async def fetch_archive_daily(
    coords: list[tuple[float, float]], start: date, end: date
) -> list[dict]:
    """Daily ERA5 history per coordinate. Returns list aligned with input order."""
    out: list[dict] = []
    deadline = asyncio.get_running_loop().time() + 150
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
            data = await _get_json_retry(
                client, settings.open_meteo_archive_url, params, deadline, "archive")
            if isinstance(data, dict):
                data = [data]
            out.extend(data)
            await asyncio.sleep(settings.fetch_pause_seconds)
    return out


async def fetch_current(coords: list[tuple[float, float]]) -> list[dict]:
    """Current conditions per coordinate (real 'current' block with timestamp).
    Throttled chunks are retried with backoff within a bounded time budget;
    whatever still fails is skipped (returned as {}) so a refresh cycle covers
    the districts it can — the scheduler retries the rest on the next cycle.
    Returns a list aligned with `coords`."""
    out: list[dict] = []
    deadline = asyncio.get_running_loop().time() + 150
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
                data = await _get_json_retry(
                    client, settings.open_meteo_forecast_url, params, deadline, "forecast")
            except httpx.HTTPStatusError as e:
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
    """GloFAS v4 daily river discharge (m3/s) from the Flood API.
    Throttled chunks are retried then skipped as {} (list stays aligned with
    `coords`); an upstream failure must not abort the whole cycle — stale
    discharge rows in the DB keep scoring alive until the next cycle."""
    out: list[dict] = []
    deadline = asyncio.get_running_loop().time() + 150
    async with httpx.AsyncClient(timeout=settings.http_timeout) as client:
        for chunk in _chunks(coords):
            params = {
                "latitude": ",".join(f"{c[0]:.4f}" for c in chunk),
                "longitude": ",".join(f"{c[1]:.4f}" for c in chunk),
                "daily": "river_discharge",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
            try:
                data = await _get_json_retry(
                    client, settings.open_meteo_flood_url, params, deadline, "discharge")
            except httpx.HTTPError as e:
                out.extend([{} for _ in chunk])
                print(f"  discharge chunk skipped ({type(e).__name__})")
                await asyncio.sleep(settings.fetch_pause_seconds)
                continue
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
