"""Export a compact recent-observations snapshot for ephemeral free-tier hosts.

Render's free plan boots with an empty database, and the risk-assessment
snapshot (last_good_risk.json) only restores the risk MAP. The History and
flood-history pages, latest-data, and the live Check-My-City / explain compute
all read the OBSERVATIONS table — which stays empty until upstream quota
allows backfilling, so those surfaces showed "no data" for hours.

This script dumps the portions of the ingested record the app actually reads
(rain_mm and soil_moisture_0_7cm for the last 90 days; river_discharge for the
last 730 days — enough for the 365d P99 flood-memory features and the
flood-history page) plus each district's elevation/slope into
data/processed/observations_snapshot.npz — a few MB of real measured values
with their true dates. app/services/history_snapshot.py seeds them into any
database whose observations table is (nearly) empty.

The windows are capped deliberately: Render's free tier has a 512 MB
ephemeral disk, and a deeper SQLite copy of history would overrun it at
boot. Raise the windows below only when targeting a host with more space.

Re-run after notable local data refreshes to refresh the shipped baseline.

Usage (from flash-flood-ai/):  python ml/data_processing/export_observations_snapshot.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
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
# Depth caps sized for Render free's 512 MB ephemeral disk (SQLite needs
# ~300 B per observation row incl. indexes; keep the seeded copy well under
# half of it). 730d of discharge covers the causal 365d P99 + flood-history;
# 90d of rain/soil covers every feature window with a wide bookmark margin.
DISCHARGE_DAYS = 730
RAIN_SOIL_DAYS = 90


def main() -> None:
    yesterday_var = {v: (db.query(func.max(Observation.observed_at))
                         .filter(Observation.variable == v).scalar()) for v in VARS}
    if not all(yesterday_var.values()):
        raise SystemExit(f"no observations to export: {yesterday_var}")
    last = max(t.date() for t in yesterday_var.values())
    per_var = {"rain_mm": RAIN_SOIL_DAYS, "soil_moisture_0_7cm": RAIN_SOIL_DAYS,
               "river_discharge": DISCHARGE_DAYS}
    since = (last - timedelta(days=max(per_var.values()) - 1))
    n_days = (last - since).days + 1
    print(f"window: {since} .. {last} ({n_days} days; "
          f"depths: {[f'{v}={d}d' for v, d in per_var.items()]})")

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
    min_date = {v: last - timedelta(days=per_var[v] - 1) for v in VARS}
    for did, var, ts, val in rows:
        i = did2row.get(did)
        j = date_idx.get(ts.date())
        if (i is None or j is None or val is None or val != val
                or ts.date() < min_date[var]):
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
