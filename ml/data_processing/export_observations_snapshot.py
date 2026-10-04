"""Export a compact recent-observations snapshot for ephemeral free-tier hosts.

Render's free plan boots with an empty database, and the risk-assessment
snapshot (last_good_risk.json) only restores the risk MAP. The History and
flood-history pages, latest-data, and the live Check-My-City / explain compute
all read the OBSERVATIONS table — which stays empty until upstream quota
allows backfilling, so those surfaces showed "no data" for hours.

This script dumps the full ingested daily record of the three display/model
variables (rain_mm, soil_moisture_0_7cm, river_discharge — locally 2020-09 to
today, union of the archive backfill and recent forecast cycles) plus each
district's elevation/slope into data/processed/observations_snapshot.npz —
a few MB of real measured values with their true dates.
app/services/history_snapshot.py seeds them into any database whose
observations table is (nearly) empty.

Re-run after notable local data refreshes to refresh the shipped baseline.

Usage (from flash-flood-ai/):  python ml/data_processing/export_observations_snapshot.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sqlalchemy import func

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import BASE_DIR  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import District, Observation  # noqa: E402

OUT_PATH = BASE_DIR / "data" / "processed" / "observations_snapshot.npz"
VARS = ["rain_mm", "soil_moisture_0_7cm", "river_discharge"]


def main() -> None:
    # Export the full ingested record: start from the earliest real observation
    # of the three variables (locally 2020-09-01) so the flood-history page and
    # the discharge-based flood-memory features see the same depth as dev.
    first = (db.query(func.min(Observation.observed_at))
             .filter(Observation.variable.in_(VARS)).scalar())
    if first is None:
        raise SystemExit("no observations to export")
    since = first.date()
    last = (db.query(func.max(Observation.observed_at))
            .filter(Observation.variable.in_(VARS)).scalar()).date()
    n_days = (last - since).days + 1
    print(f"window: {since} .. {last} ({n_days} days)")

    districts = db.query(District).all()
    shape_ids = np.array([d.shape_id for d in districts], dtype="U64")
    did2row = {d.id: i for i, d in enumerate(districts)}

    dates = pd.date_range(since, periods=n_days, freq="D")
    date_idx = {d.date(): i for i, d in enumerate(dates)}

    arrays = {v: np.full((len(districts), n_days), np.nan, dtype=np.float32) for v in VARS}
    rows = (
        db.query(Observation.district_id, Observation.variable,
                 Observation.observed_at, Observation.value)
        .filter(Observation.variable.in_(VARS),
                Observation.observed_at >= datetime.combine(since, datetime.min.time()))
        .all()
    )
    filled = 0
    for did, var, ts, val in rows:
        i = did2row.get(did)
        j = date_idx.get(ts.date())
        if i is None or j is None or val is None or val != val:
            continue
        arrays[var][i, j] = float(val)
        filled += 1

    elevation = np.array(
        [d.elevation_m if d.elevation_m is not None else np.nan for d in districts],
        dtype=np.float32)
    slope = np.array(
        [d.slope_deg if d.slope_deg is not None else np.nan for d in districts],
        dtype=np.float32)

    meta = {
        "generated_at": datetime.utcnow().isoformat(),
        "window_start": since.isoformat(),
        "window_end": last.isoformat(),
        "units": {"rain_mm": "mm", "soil_moisture_0_7cm": "m3/m3", "river_discharge": "m3/s"},
        "sources": {"rain_mm": "open-meteo-era5", "soil_moisture_0_7cm": "open-meteo-era5",
                    "river_discharge": "open-meteo-glofas"},
    }
    np.savez_compressed(
        OUT_PATH,
        shape_ids=shape_ids,
        dates=np.array([d.strftime("%Y-%m-%d") for d in dates], dtype="U10"),
        **arrays,
        elevation_m=elevation,
        slope_deg=slope,
        meta=np.array([json.dumps(meta)]),
    )

    for v in VARS:
        a = arrays[v]
        finite = np.isfinite(a)
        if finite.any():
            last_col = int(np.where(finite, np.arange(n_days), -1).max())
            last_date = dates[last_col].date()
        else:
            last_date = None
        print(f"{v}: {finite.sum()} values ({100 * finite.mean():.0f}% of {a.size}), "
              f"last valid date {last_date}")
    print(f"elevation: {np.isfinite(elevation).sum()}/{len(districts)}, "
          f"slope: {np.isfinite(slope).sum()}/{len(districts)}")
    print(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.1f} MB, "
          f"{filled} observation rows pivoted)")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        main()
    finally:
        db.close()
