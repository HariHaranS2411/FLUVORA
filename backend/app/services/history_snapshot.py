"""Observation-history fallback for ephemeral free-tier deployments.

The risk snapshot (last_good_risk.json) restores the risk MAP, but the History
and flood-history pages, latest-data and the live Check-My-City compute all
read the OBSERVATIONS table — empty on every Render free spin-up until
upstream quota permits a backfill. This module seeds the full ingested daily
record exported by ml/data_processing/export_observations_snapshot.py
(data/processed/observations_snapshot.npz: rain, soil moisture and discharge
per district per day, plus district elevation/slope) into any database whose
observations table is still (nearly) empty.

Every value is a real measurement with its true date; provenance is preserved
via the same source labels the ingest pipeline uses. Inserts are idempotent
(on the observations unique key, on-conflict-do-nothing), so a DB that already
has live rows is merely topped up, never overwritten. When upstream quota
recovers, the normal refresh pipeline simply continues appending newer rows.
"""
from __future__ import annotations

import json
from datetime import datetime, time

import numpy as np
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..models import District, Observation

SNAPSHOT_PATH = BASE_DIR / "data" / "processed" / "observations_snapshot.npz"
VARS = ("rain_mm", "soil_moisture_0_7cm", "river_discharge")
UNIT_BY_VAR = {"rain_mm": "mm", "soil_moisture_0_7cm": "m3/m3", "river_discharge": "m3/s"}
SOURCE_BY_VAR = {"rain_mm": "open-meteo-era5", "soil_moisture_0_7cm": "open-meteo-era5",
                 "river_discharge": "open-meteo-glofas"}


def _load_snapshot() -> dict | None:
    """Load the npz into memory (a few MB), or None when missing/corrupt."""
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        with np.load(SNAPSHOT_PATH, allow_pickle=False) as z:
            return {k: z[k] for k in z.files}
    except Exception as e:  # noqa: BLE001 — a defective artifact means "no fallback"
        print(f"[history-snapshot] could not read {SNAPSHOT_PATH.name}: {e}")
        return None


def seed_missing_observations(db: Session, flush_every: int = 50_000) -> int:
    """Insert every snapshot observation missing from the DB. Idempotent,
    matched by shape_id (district primary keys may differ between the
    exporting and receiving database). Also fills null elevation/slope.
    Returns the number of observation rows added."""
    data = _load_snapshot()
    if data is None:
        return 0

    shape_ids = [str(s) for s in data["shape_ids"]]
    dates = [datetime.strptime(str(d), "%Y-%m-%d") for d in data["dates"]]
    elevation = data.get("elevation_m")
    slope = data.get("slope_deg")

    from .risk_engine import _insert_observations

    id_by_shape: dict[str, int] = {}
    row_by_shape: dict[str, int] = {sid: i for i, sid in enumerate(shape_ids)}
    elev_updates: list[tuple[int, float | None, float | None]] = []
    for d in db.query(District).all():
        id_by_shape[d.shape_id] = d.id
        i = row_by_shape.get(d.shape_id)
        e = float(elevation[i]) if elevation is not None and i is not None else None
        s = float(slope[i]) if slope is not None and i is not None else None
        elev_updates.append((d.id, e if e == e else None, s if s == s else None))

    for did, e, s in elev_updates:
        row = db.get(District, did)
        if e is not None and row.elevation_m is None:
            row.elevation_m = e
        if s is not None and row.slope_deg is None:
            row.slope_deg = s
    db.commit()

    rows_before = observation_count(db)
    buffer: list[dict] = []
    attempted = 0
    now = datetime.utcnow()
    for si, sid in enumerate(shape_ids):
        did = id_by_shape.get(sid)
        if did is None:
            continue
        for var in VARS:
            arr = data[var][si]
            for j in np.where(np.isfinite(arr))[0]:
                buffer.append({
                    "district_id": did, "source": SOURCE_BY_VAR[var], "variable": var,
                    "observed_at": datetime.combine(dates[j].date(), time.min),
                    "fetched_at": now, "value": float(arr[j]),
                    "unit": UNIT_BY_VAR[var], "quality": "ok",
                })
        if len(buffer) >= flush_every:
            _insert_observations(db, buffer)
            attempted += len(buffer)
            buffer.clear()
    if buffer:
        _insert_observations(db, buffer)
        attempted += len(buffer)
    # Report what actually landed (unique-key conflicts insert nothing),
    # computed as the real row-count delta.
    added = observation_count(db) - rows_before
    if added:
        print(f"[history-snapshot] seeded {added} historical observations "
              f"({attempted} attempted, rest already present; "
              f"{len(shape_ids)} districts x {len(dates)} days window)")
    return added


def observation_count(db: Session) -> int:
    """Cheap emptiness probe used to decide whether seeding is worth it.
    The full record holds ~2.7M rows; a fresh ephemeral DB holds ~0."""
    from sqlalchemy import func
    return db.query(func.count(Observation.id)).scalar() or 0


def maybe_seed_history(db: Session, min_rows: int = 100_000) -> int:
    """Seed only when the DB looks like a fresh ephemeral boot (nearly empty).
    A no-op on any dev/paid database that already holds a real record."""
    if observation_count(db) >= min_rows:
        return 0
    return seed_missing_observations(db)
