"""Client for the GDACS event archive (EC JRC / UN) — verified live 2026-09-24.

Documented quirks handled here (see DATA_SOURCE_MATRIX.md):
- SEARCH endpoint ignores `country=` and `limit=` params and can return events
  from near-neighbour countries → strict client-side filtering by iso3 == IND.
- Date window via fromDate/toDate; we walk year-by-year to bound response size.
"""
from __future__ import annotations

from datetime import date

import httpx

from ..config import settings


def _chunk_year_ranges(start_year: int, end_year: int):
    for y in range(start_year, end_year + 1):
        yield (date(y, 1, 1), date(y, 12, 31))


async def fetch_india_flood_events(
    start_year: int = 2015, end_year: int = 2026
) -> list[dict]:
    """Fetch real India flood events from GDACS. Returns normalized dicts:
    event_id, glide, name, from_date, to_date, alert_level, lat, lon, severity."""
    events: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        for start, end in _chunk_year_ranges(start_year, end_year):
            params = {
                "eventtype": "FL",
                "fromDate": start.isoformat(),
                "toDate": end.isoformat(),
            }
            try:
                resp = await client.get(settings.gdacs_api_url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except (httpx.HTTPError, ValueError):
                continue  # window fails → skip; pipeline records source status
            features = data.get("features", []) if isinstance(data, dict) else []
            for ft in features:
                props = ft.get("properties", {})
                if str(props.get("iso3", "")).upper() != "IND":
                    continue
                if str(props.get("eventtype", "")) != "FL":
                    continue
                geom = ft.get("geometry", {}) or {}
                coords = geom.get("coordinates") or [None, None]
                from_dt = str(props.get("fromdate", ""))[:10]
                if not from_dt or coords[0] is None:
                    continue
                ev = {
                    "event_id": str(props.get("eventid")),
                    "glide": props.get("glide") or None,
                    "name": (props.get("eventname") or props.get("description") or "Flood")[:240],
                    "from_date": from_dt,
                    "to_date": str(props.get("todate", ""))[:10] or None,
                    "alert_level": props.get("alertlevel"),
                    "lat": float(coords[1]),
                    "lon": float(coords[0]),
                    "severity": (props.get("severitydata") or {}).get("severity"),
                }
                events[ev["event_id"]] = ev
    return sorted(events.values(), key=lambda e: e["from_date"])


async def check_gdacs_status() -> tuple[str, str]:
    """Live connection check for the Data Sources page."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                settings.gdacs_api_url,
                params={"eventtype": "FL", "fromDate": "2026-01-01", "toDate": "2026-12-31"},
            )
            resp.raise_for_status()
            n = len(resp.json().get("features", []))
            return "connected", f"Archive responded with {n} flood events for 2026."
    except Exception as e:  # noqa: BLE001
        return "unreachable", f"Connection check failed: {e}"
