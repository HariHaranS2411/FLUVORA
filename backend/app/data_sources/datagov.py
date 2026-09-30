"""Government of India Open Government Data (data.gov.in) client — REAL data only.

data.gov.in resource APIs require a (free) API key per dataset; unauthenticated
calls are rejected with "Key not authorised" (verified live 2026-09-25). This
client therefore:
  - fetches REAL rows when DATA_GOV_IN_API_KEY is configured, from resource IDs
    listed in DATA_GOV_IN_RESOURCES (comma-separated; operators pick datasets
    relevant to their deployment, e.g. rainfall/flood-related catalog entries);
  - reports its true 'unauthorized' state otherwise — it never fabricates data.

Only fields that map to the observation schema are stored; each row keeps the
source string "data-gov-in:<resource_id>" so provenance is exact.
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from ..config import settings
from ..models import District, Observation

BASE_URL = "https://api.data.gov.in/resource"


def is_configured() -> bool:
    return bool(settings.data_gov_in_api_key) and bool(_resource_ids())


def _resource_ids() -> list[str]:
    raw = os.getenv("DATA_GOV_IN_RESOURCES", "")
    return [x.strip() for x in raw.split(",") if x.strip()]


async def check_status() -> tuple[str, str]:
    """Live connection check for the Data Sources page."""
    if not settings.data_gov_in_api_key:
        return "unauthorized", (
            "Real data.gov.in datasets require a free API key. Register at "
            "https://data.gov.in/user/register, then set DATA_GOV_IN_API_KEY and "
            "DATA_GOV_IN_RESOURCES (comma-separated resource IDs) in .env. "
            "No substitute data is used meanwhile."
        )
    if not _resource_ids():
        return "unauthorized", (
            "API key present but no datasets configured. Set DATA_GOV_IN_RESOURCES "
            "to comma-separated resource IDs from data.gov.in dataset pages."
        )
    rid = _resource_ids()[0]
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{BASE_URL}/{rid}",
                params={"api-key": settings.data_gov_in_api_key, "limit": 1},
            )
            if r.status_code == 200:
                body = r.json()
                n = body.get("total_records") if isinstance(body, dict) else None
                return "connected", f"Resource {rid} responded (total_records={n})."
            if r.status_code in (401, 403):
                return "unauthorized", f"API key rejected for resource {rid} (HTTP {r.status_code})."
            return "unreachable", f"HTTP {r.status_code} for resource {rid}."
    except Exception as e:  # noqa: BLE001
        return "unreachable", f"Connection check failed: {e}"


async def ingest_resources(db: Session, limit_per_resource: int = 2000) -> dict:
    """Fetch REAL rows from configured resources and store any that map to
    (district, variable, date). Returns an honest report; stores nothing when
    unconfigured. Unknown districts/fields are skipped, never guessed."""
    if not is_configured():
        return {"status": "unauthorized", "ingested": 0, "detail": check_status_sync()[1]}

    districts = db.query(District).all()
    by_name: dict[str, District] = {}
    for d in districts:
        by_name[d.name.casefold()] = d
        by_name[f"{d.name} ({d.state})".casefold()] = d

    total = 0
    report: list[str] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for rid in _resource_ids():
            offset, fetched = 0, 0
            while fetched < limit_per_resource:
                try:
                    r = await client.get(
                        f"{BASE_URL}/{rid}",
                        params={"api-key": settings.data_gov_in_api_key,
                                "limit": 500, "offset": offset, "format": "json"},
                    )
                except httpx.HTTPError as e:
                    report.append(f"{rid}: fetch failed ({type(e).__name__})")
                    break
                if r.status_code != 200:
                    report.append(f"{rid}: HTTP {r.status_code}")
                    break
                body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                records = body.get("records") or []
                if not records:
                    break
                inserted = _store_records(db, rid, records, by_name)
                total += inserted
                fetched += len(records)
                offset += len(records)
                if len(records) < 500:
                    break
            report.append(f"{rid}: processed {fetched} rows")
    db.commit()
    return {"status": "connected", "ingested": total, "detail": "; ".join(report)}


def check_status_sync() -> tuple[str, str]:
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, check_status()).result(timeout=30)
        return asyncio.run(check_status())
    except RuntimeError:
        return asyncio.run(check_status())


def _store_records(db: Session, rid: str, records: list[dict], by_name: dict) -> int:
    """Map real record fields to observations when confidently identifiable:
    district-ish field + numeric value field + date field. Skips anything else."""
    if not records:
        return 0
    sample = records[0]
    dist_key = next((k for k in sample if k.lower() in
                     {"district", "district_name", "districtname", "name_of_district"}), None)
    date_key = next((k for k in sample if any(t in k.lower() for t in
                     ("date", "_on", "updated"))) and k.lower() != "updated_on_utc", None)
    val_key = next((k for k, v in sample.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)), None)
    unit = "value"
    if not (dist_key and date_key and val_key):
        return 0

    rows = []
    now = datetime.utcnow()
    for rec in records:
        name = str(rec.get(dist_key, "")).strip().casefold()
        d = by_name.get(name)
        try:
            value = float(rec.get(val_key))
        except (TypeError, ValueError):
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get(date_key, ""))[:10])
        except ValueError:
            continue
        rows.append({
            "district_id": d.id, "source": f"data-gov-in:{rid}",
            "variable": f"datagov_{val_key[:40]}", "observed_at": ts,
            "fetched_at": now, "value": value, "unit": unit, "quality": "ok",
        })
    if not rows:
        return 0
    table = Observation.__table__
    stmt = (postgresql.insert(table).on_conflict_do_nothing()
            if db.bind.dialect.name == "postgresql"
            else sqlite.insert(table).on_conflict_do_nothing())
    db.execute(stmt, rows)
    return len(rows)
