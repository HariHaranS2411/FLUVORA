"""Live source-status checks for the Data Sources page (spec §19).

Every entry is verified with a REAL request (or DB recency) when the endpoint is
public; registration-gated sources report their true configuration state. The
IMD/CWC/GSI entries record the honest probe outcome of this environment —
they are NOT shown as connected.
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..data_sources.open_meteo import prepare_open_meteo_request
from ..models import DataSourceStatus, Observation

SOURCES = [
    {"key": "open-meteo-era5", "name": "ERA5 Reanalysis (Open-Meteo Archive API)",
     "provider": "Copernicus C3S / ECMWF via Open-Meteo",
     "url": "https://archive-api.open-meteo.com/v1/archive",
     "requires_auth": False, "auth_env_var": None},
    {"key": "open-meteo-forecast", "name": "Current Weather & Soil Moisture (Open-Meteo)",
     "provider": "ECMWF/NOAA blends via Open-Meteo",
     "url": "https://api.open-meteo.com/v1/forecast",
     "requires_auth": False, "auth_env_var": None},
    {"key": "open-meteo-glofas", "name": "River Discharge (GloFAS v4 Flood API)",
     "provider": "Copernicus EMS / EC JRC via Open-Meteo",
     "url": "https://flood-api.open-meteo.com/v1/flood",
     "requires_auth": False, "auth_env_var": None},
    {"key": "open-meteo-dem", "name": "Elevation (Copernicus DEM GLO-90)",
     "provider": "Copernicus DEM via Open-Meteo",
     "url": "https://api.open-meteo.com/v1/elevation",
     "requires_auth": False, "auth_env_var": None},
    {"key": "gdacs", "name": "Historical Flood Events (GDACS Archive)",
     "provider": "EC JRC & UN GDACS", "url": "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
     "requires_auth": False, "auth_env_var": None},
    {"key": "geoboundaries", "name": "India District Boundaries (ADM2)",
     "provider": "geoBoundaries (William & Mary geoLab; source: lgdirectory.gov.in)",
     "url": "https://www.geoboundaries.org/api/current/gbOpen/IND/ADM2/",
     "requires_auth": False, "auth_env_var": None},
    {"key": "nasa-power", "name": "NASA POWER (MERRA-2) — backup rainfall source",
     "provider": "NASA Langley", "url": "https://power.larc.nasa.gov/api/temporal/daily/point",
     "requires_auth": True, "auth_env_var": "NASA_POWER_API_KEY"},
    {"key": "reliefweb", "name": "ReliefWeb Disasters API — supplementary events",
     "provider": "UN OCHA", "url": "https://api.reliefweb.int/v2/disasters",
     "requires_auth": True, "auth_env_var": "RELIEFWEB_APPNAME"},
    {"key": "data-gov-in", "name": "Open Government Data (India) — real datasets",
     "provider": "Government of India (MeitY)", "url": "https://www.data.gov.in/",
     "requires_auth": True, "auth_env_var": "DATA_GOV_IN_API_KEY"},
    {"key": "imd", "name": "India Meteorological Department",
     "provider": "Ministry of Earth Sciences, Govt of India", "url": "https://mausam.imd.gov.in/",
     "requires_auth": False, "auth_env_var": None},
    {"key": "cwc", "name": "Central Water Commission / India-WRIS",
     "provider": "Ministry of Jal Shakti, Govt of India", "url": "https://indiawris.gov.in/",
     "requires_auth": False, "auth_env_var": None},
    {"key": "gsi", "name": "Geological Survey of India (landslide hazard)",
     "provider": "Ministry of Mines, Govt of India", "url": "https://www.gsi.gov.in/",
     "requires_auth": False, "auth_env_var": None},
]


def _last_data_at(db: Session, source: str) -> datetime | None:
    return db.query(func.max(Observation.observed_at)).filter(
        Observation.source == source).scalar()


async def check_all(db: Session) -> list[DataSourceStatus]:
    results: list[DataSourceStatus] = []
    effective_urls: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for s in SOURCES:
            key = s["key"]
            status, detail = "unknown", ""
            if key in ("imd", "cwc", "gsi"):
                try:
                    r = await client.get(s["url"])
                    status = "no_public_api"
                    detail = (f"Website reachable (HTTP {r.status_code}) but no verifiable "
                              f"public machine API for the needed variables; not integrated "
                              f"rather than substituted. See DATA_SOURCE_MATRIX.md.")
                except Exception as e:  # noqa: BLE001
                    status, detail = "unreachable", f"Website probe failed: {e}"
            elif key == "nasa-power":
                if not settings.nasa_power_api_key:
                    # unauthenticated probe of a tiny real window (documented backup source)
                    try:
                        r = await client.get(
                            s["url"],
                            params={"parameters": "PRECTOTCORR", "community": "AG",
                                    "longitude": 80.27, "latitude": 13.08,
                                    "start": "20240701", "end": "20240703", "format": "JSON"})
                        status = "connected" if r.status_code == 200 else "unreachable"
                        detail = "Works without key at low volume; free key recommended (NASA_POWER_API_KEY)."
                    except Exception as e:  # noqa: BLE001
                        status, detail = "unreachable", str(e)[:300]
                else:
                    status, detail = "connected", "API key configured."
            elif key == "reliefweb":
                if not settings.reliefweb_appname:
                    status = "unauthorized"
                    detail = ("Requires an approved appname (HTTP 403 observed on probe). "
                              "Register at apidoc.reliefweb.int and set RELIEFWEB_APPNAME.")
                else:
                    try:
                        r = await client.post(
                            f"{s['url']}?appname={settings.reliefweb_appname}",
                            json={"limit": 1, "fields": {"include": ["name"]}})
                        status = "connected" if r.status_code == 200 else "unreachable"
                        detail = f"Probe HTTP {r.status_code}."
                    except Exception as e:  # noqa: BLE001
                        status, detail = "unreachable", str(e)[:300]
            elif key == "data-gov-in":
                from ..data_sources.datagov import check_status as datagov_status

                status, detail = await datagov_status()
            else:
                # public endpoints: perform a real minimal query
                try:
                    if key == "open-meteo-era5":
                        url, params = prepare_open_meteo_request(s["url"], {
                            "latitude": 13.08, "longitude": 80.27,
                            "start_date": "2024-07-01", "end_date": "2024-07-02",
                            "daily": "precipitation_sum"})
                        r = await client.get(url, params=params)
                        effective_urls[key] = url
                    elif key == "open-meteo-forecast":
                        url, params = prepare_open_meteo_request(s["url"], {
                            "latitude": 13.08, "longitude": 80.27, "current": "precipitation"})
                        r = await client.get(url, params=params)
                        effective_urls[key] = url
                    elif key == "open-meteo-glofas":
                        url, params = prepare_open_meteo_request(s["url"], {
                            "latitude": 25.43, "longitude": 81.83, "daily": "river_discharge",
                            "start_date": "2024-07-01", "end_date": "2024-07-02"})
                        r = await client.get(url, params=params)
                        effective_urls[key] = url
                    elif key == "open-meteo-dem":
                        url, params = prepare_open_meteo_request(
                            s["url"], {"latitude": 13.08, "longitude": 80.27})
                        r = await client.get(url, params=params)
                        effective_urls[key] = url
                    elif key == "gdacs":
                        r = await client.get(s["url"], params={
                            "eventtype": "FL", "fromDate": "2026-01-01", "toDate": "2026-12-31"})
                        n = len(r.json().get("features", []))
                        detail = f"Archive responded with {n} flood events for 2026."
                    elif key == "geoboundaries":
                        r = await client.get(s["url"])
                    if r.status_code == 429:
                        status = "rate_limited"
                        detail = ("Endpoint reachable but the free-tier request quota is "
                                  "exhausted; the pipeline resumes in the next quota window.")
                    else:
                        r.raise_for_status()
                        status = "connected"
                        if key != "gdacs":
                            detail = f"Live probe HTTP {r.status_code}."
                except Exception as e:  # noqa: BLE001
                    status = "unreachable"
                    code = getattr(getattr(e, "response", None), "status_code", None)
                    detail = (f"Open-Meteo probe HTTP {code}." if code and key.startswith("open-meteo-")
                              else f"Probe failed ({type(e).__name__})." if key.startswith("open-meteo-")
                              else str(e)[:300])

            src_map = {"open-meteo-era5": "open-meteo-era5",
                       "open-meteo-forecast": "open-meteo-forecast",
                       "open-meteo-glofas": "open-meteo-glofas"}
            row = db.query(DataSourceStatus).filter(DataSourceStatus.key == key).first()
            if row is None:
                row = DataSourceStatus(key=key, name=s["name"], provider=s["provider"],
                                       url=effective_urls.get(key, s["url"]), requires_auth=s["requires_auth"],
                                       auth_env_var=s["auth_env_var"])
                db.add(row)
            row.name, row.provider = s["name"], s["provider"]
            row.url = effective_urls.get(key, s["url"])
            row.status = status
            row.detail = detail
            row.last_checked_at = datetime.utcnow()
            if status == "connected":
                row.last_success_at = row.last_checked_at
            ld = _last_data_at(db, src_map.get(key, key)) if key in src_map else None
            if ld:
                row.last_data_at = ld
            results.append(row)
    db.commit()
    return results
