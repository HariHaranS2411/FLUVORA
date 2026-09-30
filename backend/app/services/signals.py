"""Signal components of the risk pipeline (REAL data only).

Pipeline position:
    REAL DATA -> DATA QUALITY -> FEATURES -> RAIN SIGNAL -> HYDROLOGICAL SIGNAL
    -> HISTORICAL CONTEXT (this module) -> RISK FUSION -> RISK LEVEL -> EXPLANATION

This module owns:
  - discharge_daily / hydro_memory : shared real-series helpers (single owner,
    used by the feature builder, the history endpoint and the risk engine)
  - discharge_condition            : current river state vs its real seasonal
    baseline, with an honest trend and record percentile
  - historical_analogues           : transparent similarity between today's
    conditions and the district's real past high-flow episodes

Nothing here invents values; every number traces to ingested observations or
the trained dataset built from them.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..models import District, Observation

PROCESSED = BASE_DIR / "data" / "processed"

# ---------------------------------------------------------------- shared series

def discharge_daily(db: Session, district_id: int) -> dict:
    """Full real daily discharge series (last value per day) for the district."""
    rows = (
        db.query(Observation)
        .filter(Observation.district_id == district_id,
                Observation.variable == "river_discharge")
        .with_entities(Observation.observed_at, Observation.value)
        .order_by(Observation.observed_at)
        .all()
    )
    out: dict = {}
    for ts, v in rows:
        out[ts.date()] = float(v)  # later rows overwrite: last-of-day wins
    return out


def hydro_memory(dis: dict, today) -> tuple[float, float, float, object]:
    """Causal flood-memory features from the real daily series, mirroring the
    training definitions (threshold = past-365d P99, shifted 1 day).
    Returns (exceedance_30d, exceedance_90d, days_since_last, last_ex_date)."""
    ex30 = ex90 = 0.0
    days_since = 730.0
    last_ex_date = None
    dates_all = sorted(dis)
    if len(dates_all) >= 120:
        vals = pd.Series([dis[x] for x in dates_all], index=dates_all, dtype=float)
        q = vals.shift(1).rolling(365, min_periods=120).quantile(0.99)
        ex = (vals >= q) & q.notna()
        for d in dates_all:
            if d >= today:
                continue
            lag = (today - d).days
            if bool(ex.loc[d]):
                if 1 <= lag <= 30:
                    ex30 += 1
                if 1 <= lag <= 90:
                    ex90 += 1
                last_ex_date = d
        if last_ex_date is not None:
            days_since = float((today - last_ex_date).days)
    return ex30, ex90, days_since, last_ex_date


# ------------------------------------------------------------ discharge signal

def discharge_condition(db: Session, d: District, now: datetime,
                        dis: dict | None = None) -> dict | None:
    """Honest river-state summary from the real series + real climatology:
    current value, seasonal baseline (monthly mean/median of the district's own
    ingested record), ratio, 7-day trend, and where today sits in the record."""
    dis = dis if dis is not None else discharge_daily(db, d.id)
    if not dis:
        return None
    dates = sorted(dis)
    today_d = now.date()
    current = dis.get(today_d)
    if current is None:  # today's value not ingested yet: use the latest real one
        current = dis[dates[-1]]
        current_date = dates[-1]
    else:
        current_date = today_d

    # seasonal baseline from the district's own full ingested record
    same_month = [dis[x] for x in dates if x.month == current_date.month]
    mean_m = float(np.mean(same_month)) if same_month else None
    med_m = float(np.median(same_month)) if same_month else None

    # 7-day trend: robust slope of the last week of real daily values
    week = [dis[x] for x in dates if 0 <= (current_date - x).days <= 6]
    trend = "stable"
    trend_pct_per_day = None
    if len(week) >= 4:
        xs = np.arange(len(week), dtype=float)
        slope = float(np.polyfit(xs, np.array(week, dtype=float), 1)[0])
        base = float(np.mean(week)) or 1.0
        trend_pct_per_day = round(100 * slope / base, 1)
        if trend_pct_per_day > 5:
            trend = "rising"
        elif trend_pct_per_day < -5:
            trend = "falling"

    rank = sum(1 for x in dates if dis[x] <= current)
    return {
        "current_m3s": round(current, 2),
        "current_date": current_date.isoformat(),
        "record_days": len(dates),
        "record_first": dates[0].isoformat(),
        "seasonal_mean_m3s": round(mean_m, 2) if mean_m is not None else None,
        "seasonal_median_m3s": round(med_m, 2) if med_m is not None else None,
        "ratio_to_seasonal_mean": (round(current / mean_m, 2)
                                   if mean_m not in (None, 0) else None),
        "ratio_to_seasonal_median": (round(current / med_m, 2)
                                     if med_m not in (None, 0) else None),
        "trend": trend,
        "trend_pct_per_day": trend_pct_per_day,
        "record_percentile": round(100 * rank / len(dates), 1),
    }


# --------------------------------------------------------- historical analogues

_ds_cache: dict = {}
_ANALOGUE_FEATURES = ["rain_7d", "soil_saturation", "discharge_ratio_mean"]


def _episode_frame() -> pd.DataFrame:
    """Historical district-day feature frame from the trained dataset (cached).
    Only real precomputed features are used; the dataset itself is built from
    ingested observations with causal (lagged) feature definitions."""
    if "df" not in _ds_cache:
        path = PROCESSED / "dataset.pkl"
        if not path.exists():
            _ds_cache["df"] = pd.DataFrame()
        else:
            df = pd.read_pickle(path)
            cols = ["district_id", "date", "y_hydro", "split", "month",
                    *_ANALOGUE_FEATURES]
            _ds_cache["df"] = df[cols]
    return _ds_cache["df"]


def historical_analogues(db: Session, d: District, feats: dict,
                         now: datetime, k: int = 5) -> dict | None:
    """Compare today's real conditions with the district's real past high-flow
    episodes (y_hydro days in the TRAIN/VAL splits — strictly historical).

    Similarity is a transparent normalized distance over the listed features:
    each |current - episode| is divided by that feature's interquartile range
    across ALL of the district's ingested days (its natural spread of weather),
    plus a small seasonal-distance term:
        similarity = max(0, 1 - mean(normalized distances) - 0.25*seasonal) * 100
    Similarity is a descriptive comparison — it does NOT mean a flood will
    occur, and the returned text says so."""
    df = _episode_frame()
    if df.empty:
        return None
    mine = df[df["district_id"] == d.shape_id]
    if mine.empty:
        return None  # district not covered by the historical feature dataset
    ep = mine[(mine["y_hydro"] == 1) & (mine["split"].isin(["train", "val"]))].copy()
    if len(ep) < 10:
        return None

    cur = {f: feats.get(f) for f in _ANALOGUE_FEATURES}
    if any(cur[f] is None or pd.isna(cur[f]) for f in _ANALOGUE_FEATURES):
        return None

    # scales from ALL of the district's days (natural weather spread), so the
    # score stays meaningful even when current conditions are quiet
    scales: dict[str, float] = {}
    for f in _ANALOGUE_FEATURES:
        v = mine[f].dropna()
        iqr = float(v.quantile(0.75) - v.quantile(0.25)) if len(v) else 0.0
        scales[f] = iqr if iqr > 1e-9 else (float(v.std()) if len(v) > 1 else 1.0)
        if not scales[f] or scales[f] <= 1e-9:
            scales[f] = 1.0

    def dist(row) -> float:
        terms = [abs(cur[f] - row[f]) / scales[f] for f in _ANALOGUE_FEATURES]
        dm = abs(int(row["month"]) - now.month)
        seasonal = min(dm, 12 - dm) / 6.0
        return float(np.mean(terms)) + 0.25 * seasonal

    ep["distance"] = ep.apply(dist, axis=1)
    ep["similarity"] = (1.0 - ep["distance"]).clip(0, 1) * 100
    ep = ep.sort_values("similarity", ascending=False)

    top = []
    for _, r in ep.head(k).iterrows():
        top.append({
            "date": pd.Timestamp(r["date"]).date().isoformat(),
            "rain_7d_mm": None if pd.isna(r["rain_7d"]) else round(float(r["rain_7d"]), 1),
            "soil_saturation": None if pd.isna(r["soil_saturation"]) else round(float(r["soil_saturation"]), 2),
            "discharge_ratio_mean": None if pd.isna(r["discharge_ratio_mean"]) else round(float(r["discharge_ratio_mean"]), 2),
            "similarity_pct": round(float(r["similarity"]), 1),
        })
    n_similar = int((ep["similarity"] >= 70).sum())
    return {
        "episodes": top,
        "n_past_episodes": int(len(ep)),
        "n_similar_episodes": n_similar,
        "share_of_similar_episodes_pct": round(100 * n_similar / len(ep), 1),
        "features_used": _ANALOGUE_FEATURES,
        "method": ("normalized-distance similarity over the listed features "
                   "(each |now - past| divided by that feature's interquartile "
                   "range across this district's ingested days, plus a seasonal "
                   "term). Descriptive only — similar past conditions do not "
                   "guarantee future flooding."),
    }
