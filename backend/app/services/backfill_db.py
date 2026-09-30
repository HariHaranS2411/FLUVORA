"""Backfill the DB's observations table from the REAL ingested history pickles
(ERA5 daily + GloFAS discharge) and attach real elevation/slope to districts.
Run once after ingest_history; idempotent (merge on unique key)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..config import BASE_DIR
from ..database import SessionLocal, engine
from ..models import Base, District, Observation

PROCESSED = BASE_DIR / "data" / "processed"


def _df_from_archive(entries: list[dict]) -> pd.DataFrame:
    var_map = {
        "precipitation_sum": ("rain_mm", "mm"),
        "soil_moisture_0_to_7cm_mean": ("soil_moisture_0_7cm", "m3/m3"),
    }
    frames = []
    for idx, entry in enumerate(entries):
        daily = entry.get("daily") or {}
        times = daily.get("time") or []
        if not times:
            continue
        df = pd.DataFrame({"date": pd.to_datetime(times).date, "loc_idx": idx})
        for api_name, (col, _unit) in var_map.items():
            df[col] = daily.get(api_name) or [None] * len(times)
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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
        daily = _df_from_archive(era5)
        now = datetime.utcnow()
        unit_map = {"rain_mm": "mm", "soil_moisture_0_7cm": "m3/m3"}
        rows = []
        for row in daily.itertuples(index=False):
            did = id_by_idx.get(row.loc_idx)
            if did is None:
                continue
            ts = datetime.combine(row.date, datetime.min.time())
            for col in ("rain_mm", "soil_moisture_0_7cm"):
                v = getattr(row, col)
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                rows.append({
                    "district_id": did, "source": "open-meteo-era5", "variable": col,
                    "observed_at": ts, "fetched_at": now, "value": float(v),
                    "unit": unit_map[col], "quality": "ok"})
        _bulk_insert(db, Observation, rows)
        print(f"era5 observations ensured: {len(rows)}")

        # GloFAS discharge (chunked bulk insert; idempotent)
        glofas = pd.read_pickle(PROCESSED / "glofas_daily.pkl")
        rows = []
        for idx, entry in enumerate(glofas):
            did = id_by_idx.get(idx)
            if did is None:
                continue
            daily_g = entry.get("daily") or {}
            for t, v in zip(daily_g.get("time") or [], daily_g.get("river_discharge") or []):
                if v is None:
                    continue
                rows.append({
                    "district_id": did, "source": "open-meteo-glofas",
                    "variable": "river_discharge", "observed_at": datetime.fromisoformat(t),
                    "fetched_at": now, "value": float(v), "unit": "m3/s", "quality": "ok"})
        _bulk_insert(db, Observation, rows)
        print(f"discharge observations ensured: {len(rows)}")
    finally:
        db.close()


if __name__ == "__main__":
    purge_backfill_observations()
    backfill()
