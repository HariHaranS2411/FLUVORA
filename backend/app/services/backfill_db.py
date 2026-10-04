"""Backfill the DB's observations table from the REAL ingested history pickles
(ERA5 daily + GloFAS discharge) and attach real elevation/slope to districts.
Idempotent: rows merge on the unique key, and once historical rows exist the
pass exits early (keeps repeated runs — e.g. Render pre-deploys — fast).
Run once after ingest_history.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import func

from ..config import BASE_DIR
from ..database import SessionLocal, engine
from ..models import Base, District, Observation

PROCESSED = BASE_DIR / "data" / "processed"

# Districts per insert pass. Building rows for ALL 735 districts at once needs
# ~2 GB of dicts; chunking keeps peak memory well inside Render's 2 GB plan.
CHUNK_DISTRICTS = 100

# A full backfill writes ~8M rows; live cycles write the same source tags but
# only recent days. Crossing 1M therefore means history is already backfilled.
BACKFILLED_ROW_THRESHOLD = 1_000_000


def _bulk_insert(db, model, rows: list[dict], chunk: int = 20000) -> None:
    """Fast idempotent insert: ON CONFLICT/IGNORE per dialect."""
    from sqlalchemy.dialects import postgresql, sqlite

    table = model.__table__
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        if db.bind.dialect.name == "postgresql":
            stmt = postgresql.insert(table).on_conflict_do_nothing()
        else:
            stmt = sqlite.insert(table).on_conflict_do_nothing()
        db.execute(stmt, part)
        db.commit()


def purge_backfill_observations() -> None:
    """Remove previously misattributed backfill rows (old position-based mapping).
    Live forecast rows (correctly keyed) are kept."""
    db = SessionLocal()
    try:
        n = db.query(Observation).filter(
            Observation.source.in_(["open-meteo-era5", "open-meteo-glofas"])).delete(
            synchronize_session=False)
        db.commit()
        print(f"purged {n} misattributed observation rows")
    finally:
        db.close()


def backfill() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        existing = (
            db.query(func.count(Observation.id))
            .filter(Observation.source.in_(["open-meteo-era5", "open-meteo-glofas"]))
            .scalar()
            or 0
        )
        if existing > BACKFILLED_ROW_THRESHOLD:
            print(f"backfill: historical rows already present ({existing}) — skipping")
            return
        districts = db.query(District).all()
        by_shape = {d.shape_id: d for d in districts}
        # CRITICAL: ingest pickles are ordered by districts.csv row order (sorted by
        # state/name). Map loc_idx -> shape_id via the csv, NOT by DB enumeration
        # order, or history lands on the wrong districts.
        import csv as _csv

        csv_order = []
        with open(PROCESSED / "districts.csv", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                csv_order.append(row["shape_id"])
        id_by_idx = {i: by_shape[sid].id for i, sid in enumerate(csv_order) if sid in by_shape}
        shape_by_idx = {i: sid for i, sid in enumerate(csv_order)}

        # attach real terrain values (same csv-order mapping)
        elevations = pd.read_pickle(PROCESSED / "elevation.pkl")
        slopes = pd.read_pickle(PROCESSED / "slope.pkl") if (PROCESSED / "slope.pkl").exists() else None
        for i, did in id_by_idx.items():
            d = db.get(District, did)
            if d is None:
                continue
            d.elevation_m = float(elevations[i]) if i < len(elevations) else None
            if slopes is not None and i < len(slopes):
                d.slope_deg = float(slopes[i])
        db.commit()
        print("terrain attached (csv-order mapping)")

        # ERA5 daily observations (chunked bulk insert; idempotent)
        era5 = pd.read_pickle(PROCESSED / "era5_daily.pkl")
        now = datetime.utcnow()
        unit_map = {"rain_mm": "mm", "soil_moisture_0_7cm": "m3/m3"}
        total = 0
        for start in range(0, len(era5), CHUNK_DISTRICTS):
            rows = []
            for idx in range(start, min(start + CHUNK_DISTRICTS, len(era5))):
                did = id_by_idx.get(idx)
                if did is None:
                    continue
                daily_e = era5[idx].get("daily") or {}
                times = daily_e.get("time") or []
                rains = daily_e.get("precipitation_sum") or [None] * len(times)
                soils = daily_e.get("soil_moisture_0_to_7cm_mean") or [None] * len(times)
                for t, rain, soil in zip(times, rains, soils):
                    ts = datetime.combine(pd.to_datetime(t).date(), datetime.min.time())
                    for col, v in (("rain_mm", rain), ("soil_moisture_0_7cm", soil)):
                        if v is None or (isinstance(v, float) and np.isnan(v)):
                            continue
                        rows.append({
                            "district_id": did, "source": "open-meteo-era5", "variable": col,
                            "observed_at": ts, "fetched_at": now, "value": float(v),
                            "unit": unit_map[col], "quality": "ok"})
            _bulk_insert(db, Observation, rows)
            total += len(rows)
        print(f"era5 observations ensured: {total}")

        # GloFAS discharge (chunked bulk insert; idempotent)
        glofas = pd.read_pickle(PROCESSED / "glofas_daily.pkl")
        total = 0
        for start in range(0, len(glofas), CHUNK_DISTRICTS):
            rows = []
            for idx in range(start, min(start + CHUNK_DISTRICTS, len(glofas))):
                did = id_by_idx.get(idx)
                if did is None:
                    continue
                daily_g = glofas[idx].get("daily") or {}
                for t, v in zip(daily_g.get("time") or [], daily_g.get("river_discharge") or []):
                    if v is None:
                        continue
                    rows.append({
                        "district_id": did, "source": "open-meteo-glofas",
                        "variable": "river_discharge", "observed_at": datetime.fromisoformat(t),
                        "fetched_at": now, "value": float(v), "unit": "m3/s", "quality": "ok"})
            _bulk_insert(db, Observation, rows)
            total += len(rows)
        print(f"discharge observations ensured: {total}")
    finally:
        db.close()


if __name__ == "__main__":
    purge_backfill_observations()
    backfill()
