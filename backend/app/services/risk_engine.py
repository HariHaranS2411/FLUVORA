"""Current Risk Engine (spec §11, §29) — uses ONLY real latest data.

latest real observations -> current features -> trained model -> calibrated
probability -> documented thresholds -> trend -> historical similarity ->
per-variable freshness. Missing critical inputs => probability=None and
risk DATA_UNAVAILABLE; values are never fabricated.

HONEST MEANING OF THE OUTPUT
----------------------------
The deployed model's target is y_hydro: "GloFAS discharge >= the district's
casual 5-year rolling 99th percentile" — a MODELED HYDROLOGICAL THRESHOLD,
not an officially observed flood. The probability is therefore presented as
a hydrological threshold exceedance / high-flow estimate. It is never a
probability that a real flood will occur. Features are computed from
observations up to the data cut, so the estimate is a NOWCAST for the
current day (0-24 h horizon).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..models import District, FloodEvent, Observation, RiskAssessment
from .signals import (
    discharge_condition, discharge_daily as _discharge_daily,
    historical_analogues, hydro_memory as _hydro_memory,
)

# The deployed target labels the SAME day's high-flow threshold exceedance;
# features use observations up to the data cut. Horizon is therefore a nowcast.
PREDICTION_HORIZON = "0–24 h (nowcast of today's high-flow state)"
TARGET_DEFINITIONS = {
    "y": "Officially reported flood event (GDACS) for the district within [t, t+3]",
    "y_hydro": ("GloFAS river discharge >= the district's causal 5-year rolling "
                "99th percentile — a modeled hydrological threshold, NOT an "
                "officially observed flood"),
}

PROCESSED = BASE_DIR / "data" / "processed"
MODEL_PATH = BASE_DIR / "ml" / "models" / "model.joblib"

FEATURES = [
    "rain_1d", "rain_2d", "rain_3d", "rain_5d", "rain_7d",
    "rainfall_change", "rainfall_acceleration", "recent_vs_previous",
    "api_7", "rain_hours_3d",
    "soil_moisture", "soil_saturation", "soil_moisture_change",
    "discharge", "discharge_ratio_mean", "discharge_ratio_median",
    "elevation_m", "slope_deg", "month", "hist_flood_freq_365d",
    "flood_reports_90d", "exceedance_30d", "exceedance_90d",
    "days_since_last_exceedance",
]
MISSING_CRITICAL = {"rain_1d", "rain_7d", "soil_moisture", "discharge"}

FEATURE_LABELS = {
    "rain_1d": ("Rainfall today", "mm"),
    "rain_2d": ("Rainfall, 2-day total", "mm"),
    "rain_3d": ("Rainfall, 3-day total", "mm"),
    "rain_5d": ("Rainfall, 5-day total", "mm"),
    "rain_7d": ("Rainfall, 7-day total", "mm"),
    "rainfall_change": ("Rainfall change vs yesterday", "mm"),
    "rainfall_acceleration": ("Rainfall acceleration (2-day)", "mm"),
    "recent_vs_previous": ("This week vs previous week", "mm"),
    "api_7": ("Antecedent rainfall index (memory)", "mm"),
    "rain_hours_3d": ("Rain duration, 3-day", "h"),
    "soil_moisture": ("Soil moisture, 0–7 cm", "m³/m³"),
    "soil_saturation": ("Soil saturation vs local max", "ratio"),
    "soil_moisture_change": ("Soil moisture change, 3-day", "m³/m³"),
    "discharge": ("River discharge", "m³/s"),
    "discharge_ratio_mean": ("Discharge vs seasonal mean", "ratio"),
    "discharge_ratio_median": ("Discharge vs seasonal median", "ratio"),
    "elevation_m": ("Elevation", "m"),
    "slope_deg": ("Local slope", "°"),
    "month": ("Season (month)", ""),
    "hist_flood_freq_365d": ("Flood reports, past year", "count"),
    "flood_reports_90d": ("Flood-report days, past 90 days", "days"),
    "exceedance_30d": ("Flood-level river days, past 30 days", "days"),
    "exceedance_90d": ("Flood-level river days, past 90 days", "days"),
    "days_since_last_exceedance": ("Days since last flood-level day", "days"),
}

_cache: dict = {}


def _load() -> dict:
    if not _cache:
        _cache["bundle"] = joblib.load(MODEL_PATH)
        _cache["card"] = json.loads((PROCESSED / "model_card.json").read_text())
        _cache["stats"] = pd.read_csv(PROCESSED / "historical_stats.csv", index_col=0)
        _cache["clim"] = pd.read_csv(PROCESSED / "discharge_climatology.csv")
    return _cache


def _window(db: Session, district_id: int, variable: str, hours: int):
    since = datetime.utcnow() - timedelta(hours=hours)
    return (
        db.query(Observation)
        .filter(Observation.district_id == district_id,
                Observation.variable == variable,
                Observation.observed_at >= since)
        .order_by(Observation.observed_at)
        .all()
    )


# discharge series + hydro memory now live in services/signals.py (single owner)
# and are imported above as _discharge_daily / _hydro_memory.
def _features(db: Session, d: District, now: datetime):
    rain = {o.observed_at.date(): o.value for o in _window(db, d.id, "rain_mm", 24 * 10)}
    sm = {o.observed_at.date(): o.value for o in _window(db, d.id, "soil_moisture_0_7cm", 24 * 10)}
    dis = _discharge_daily(db, d.id)  # full real series for flood-memory features

    def days(by_day: dict, n: int) -> list:
        return [by_day.get((now - timedelta(days=k)).date()) for k in range(n - 1, -1, -1)]

    rain_days = days(rain, 10)
    sm_days = days(sm, 8)
    dis_days = days(dis, 70)
    cuts = [max(m) for m in (rain, sm, dis) if m]
    cut = max(cuts) if cuts else None

    s = pd.Series(rain_days, dtype=float)
    smax_all = db.query(func.max(Observation.value)).filter(
        Observation.district_id == d.id,
        Observation.variable == "soil_moisture_0_7cm").scalar()
    sm_s = pd.Series(sm_days, dtype=float)
    dis_s = pd.Series(dis_days, dtype=float)

    f: dict = {}
    f["rain_1d"] = s.iloc[-1]
    f["rain_2d"] = s.tail(2).sum(min_count=1)
    f["rain_3d"] = s.tail(3).sum(min_count=1)
    f["rain_5d"] = s.tail(5).sum(min_count=1)
    f["rain_7d"] = s.tail(7).sum(min_count=1)
    f["rainfall_change"] = s.iloc[-1] - s.iloc[-2] if s.notna().sum() >= 2 else np.nan
    f["rainfall_acceleration"] = ((s.iloc[-1] - s.iloc[-2]) - (s.iloc[-2] - s.iloc[-3])
                                  if s.notna().sum() >= 3 else np.nan)
    f["recent_vs_previous"] = (s.tail(7).sum(min_count=1) - s.iloc[-10:-3].sum(min_count=1)
                               if s.notna().sum() >= 4 else np.nan)
    f["api_7"] = float(s.ewm(alpha=1 - math.exp(-0.2), adjust=False).mean().iloc[-1])
    f["rain_hours_3d"] = np.nan  # hourly rain-hours not stored; documented as N/A

    if sm_s.notna().any():
        last = sm_s.dropna().iloc[-1]
        f["soil_moisture"] = last
        f["soil_saturation"] = last / float(smax_all) if smax_all else np.nan
        prev = sm_s.iloc[-4] if len(sm_s) >= 4 else np.nan
        f["soil_moisture_change"] = last - prev if pd.notna(prev) else np.nan
    else:
        f["soil_moisture"] = f["soil_saturation"] = f["soil_moisture_change"] = np.nan

    if dis_s.notna().any():
        clim = _load()["clim"]
        row = clim[(clim["district_id"] == d.shape_id) & (clim["month"] == now.month)]
        mean_m = float(row["dis_mean_month"].iloc[0]) if not row.empty else np.nan
        med_m = float(row["dis_median_month"].iloc[0]) if not row.empty else np.nan
        last = dis_s.dropna().iloc[-1]
        f["discharge"] = last
        f["discharge_ratio_mean"] = last / mean_m if mean_m == mean_m else np.nan
        f["discharge_ratio_median"] = last / med_m if med_m == med_m else np.nan
    else:
        f["discharge"] = f["discharge_ratio_mean"] = f["discharge_ratio_median"] = np.nan

    f["elevation_m"] = d.elevation_m if d.elevation_m is not None else np.nan
    f["slope_deg"] = d.slope_deg if d.slope_deg is not None else np.nan
    f["month"] = now.month
    cnt = db.query(FloodEvent).filter(
        FloodEvent.matched_district_id == d.id,
        FloodEvent.from_date >= now - timedelta(days=365),
        FloodEvent.from_date <= now).count()
    f["hist_flood_freq_365d"] = float(cnt)

    # Flood-report days within the past 90 days (real GDACS events, capped at 7d each)
    ev_days = 0.0
    for e in (db.query(FloodEvent)
              .filter(FloodEvent.matched_district_id == d.id,
                      FloodEvent.from_date >= now - timedelta(days=90),
                      FloodEvent.from_date <= now).all()):
        start = e.from_date.date()
        end = e.to_date.date() if e.to_date else start
        end = min(end, start + timedelta(days=7), now.date())
        ev_days += max(0, (end - start).days + 1)
    f["flood_reports_90d"] = float(ev_days)

    # Causal hydrological flood memory from the real discharge series
    ex30, ex90, days_since, _last_ex = _hydro_memory(dis, now.date())
    f["exceedance_30d"] = ex30
    f["exceedance_90d"] = ex90
    f["days_since_last_exceedance"] = days_since

    freshness = {}
    for name, m in [("rain", rain), ("soil_moisture", sm), ("river_discharge", dis)]:
        if not m:
            freshness[name] = "unavailable"
        elif max(m) >= (now - timedelta(hours=36)).date():
            freshness[name] = "fresh"
        else:
            freshness[name] = "stale"
    return f, freshness, cut


def _pctl_desc(feat: str, val: float, stats: pd.DataFrame) -> str | None:
    if pd.isna(val) or feat not in stats.index:
        return None
    row = stats.loc[feat]
    labels = [("p99", "above the 99th percentile of historical days"),
              ("p95", "above the 95th percentile of historical days"),
              ("p90", "above the 90th percentile of historical days"),
              ("p75", "above the 75th percentile of historical days"),
              ("p50", "above the historical median")]
    for key, text in labels:
        if val >= row[key]:
            return text
    return None


def _rain_signal(feats: dict, stats: pd.DataFrame) -> dict:
    """Rainfall signal: how unusual today's rain accumulations are vs the
    district's historical (train-split) percentiles. Real values only."""
    def pctl(feat: str) -> float | None:
        v = feats.get(feat)
        if v is None or pd.isna(v) or feat not in stats.index:
            return None
        row = stats.loc[feat]
        for key, p in (("p99", 99), ("p95", 95), ("p90", 90), ("p75", 75), ("p50", 50)):
            if v >= row[key]:
                return p
        return 0

    r1, r7 = pctl("rain_1d"), pctl("rain_7d")
    pcts = [p for p in (r1, r7, pctl("rain_3d"), pctl("rain_5d")) if p is not None]
    level = max(pcts) if pcts else None
    desc = {None: "unknown (rainfall data missing)",
            0: "no rain / below the historical median",
            50: "about the historical median",
            75: "above the 75th percentile of historical days",
            90: "above the 90th percentile of historical days",
            95: "above the 95th percentile of historical days",
            99: "above the 99th percentile of historical days"}
    return {
        "level_percentile": level,
        "description": desc[level],
        "rain_1d_mm": None if pd.isna(feats.get("rain_1d")) else round(float(feats["rain_1d"]), 1),
        "rain_7d_mm": None if pd.isna(feats.get("rain_7d")) else round(float(feats["rain_7d"]), 1),
    }


def _soil_signal(feats: dict, stats: pd.DataFrame) -> dict:
    """Soil signal in honest terms: current value, historical percentile and
    relative wetness. No arbitrary '% soaked' conversion."""
    sm = feats.get("soil_moisture")
    sat = feats.get("soil_saturation")
    if sm is None or pd.isna(sm):
        return {"status": "no soil-moisture data"}
    pctl = None
    if "soil_saturation" in stats.index and sat is not None and not pd.isna(sat):
        row = stats.loc["soil_saturation"]
        for key, p in (("p99", 99), ("p95", 95), ("p90", 90), ("p75", 75), ("p50", 50)):
            if sat >= row[key]:
                pctl = p
                break
        pctl = pctl or 0
    if pctl is None:
        rel = "wetness unknown (no historical reference available)"
    elif pctl >= 90:
        rel = "above the historical level for this district (top 10%)"
    elif pctl >= 75:
        rel = "above the historical level for this district"
    elif pctl >= 50:
        rel = "about the historical median for this district"
    else:
        rel = "below the historical median for this district"
    chg = feats.get("soil_moisture_change")
    return {
        "soil_moisture_m3m3": round(float(sm), 3),
        "relative_wetness_percentile": pctl,
        "description": f"Soil moisture is currently {rel}.",
        "change_3d": None if chg is None or pd.isna(chg) else round(float(chg), 3),
    }


def match_gdacs_name(db: Session, d: District, start, end) -> dict | None:
    """Find a real reported event (flood or named cyclone) matching this episode:
    same district, or within ~2.5 degrees (cyclone tracks are often offshore),
    with an overlapping report window (±3 days). Returns real event metadata or
    None — names are never invented."""
    from datetime import timedelta as _td

    lo = start - _td(days=3)
    hi = end + _td(days=3)

    def _hit(ev):
        ev_end = ev.to_date.date() if ev.to_date else ev.from_date.date()
        return ev.from_date.date() <= hi and ev_end >= lo

    ev = (db.query(FloodEvent)
          .filter(FloodEvent.matched_district_id == d.id)
          .order_by(FloodEvent.from_date.desc()).all())
    for e in ev:
        if _hit(e):
            return {"name": e.name, "glide": e.glide, "alert_level": e.alert_level,
                    "source": e.source, "event_id": e.event_id}
    near = (db.query(FloodEvent)
            .filter(FloodEvent.from_date <= hi,
                    FloodEvent.lat.between(d.lat - 2.5, d.lat + 2.5),
                    FloodEvent.lon.between(d.lon - 2.5, d.lon + 2.5))
            .all())
    best, bd = None, 1e9
    for e in near:
        if not _hit(e):
            continue
        dist = ((e.lat - d.lat) ** 2 + (e.lon - d.lon) ** 2) ** 0.5
        if dist < bd:
            best, bd = e, dist
    if best:
        return {"name": best.name, "glide": best.glide, "alert_level": best.alert_level,
                "source": best.source + "-nearby", "event_id": best.event_id}
    return None


def _last_flood_context(db: Session, d: District, feats: dict, now: datetime) -> dict | None:
    """Analyse the real climatic conditions during this district's most recent
    flood (a GDACS-reported event or a GloFAS flood-level day, whichever is
    more recent) and compare them with today's conditions."""
    dis = _discharge_daily(db, d.id)
    _, _, _, hydro_date = _hydro_memory(dis, now.date())

    ev = (db.query(FloodEvent)
          .filter(FloodEvent.matched_district_id == d.id, FloodEvent.from_date <= now)
          .order_by(FloodEvent.from_date.desc()).first())
    ev_date = ev.from_date.date() if ev else None

    if ev_date is None and hydro_date is None:
        return None
    if hydro_date is not None and (ev_date is None or hydro_date >= ev_date):
        L, kind = hydro_date, "flood-level river discharge (GloFAS threshold)"
        m = match_gdacs_name(db, d, L - timedelta(days=2), L + timedelta(days=5))
        name = m["name"] if m else None
        glide = m["glide"] if m else None
        matched = m
    else:
        L, kind, name = ev_date, "reported flood event (GDACS)", ev.name
        glide, matched = ev.glide, None

    lo = datetime.combine(L - timedelta(days=2), datetime.min.time())
    hi = datetime.combine(min(L + timedelta(days=5), now.date()), datetime.min.time())
    rows = (db.query(Observation)
            .filter(Observation.district_id == d.id,
                    Observation.observed_at >= lo, Observation.observed_at <= hi,
                    Observation.variable.in_(
                        ["rain_mm", "soil_moisture_0_7cm", "river_discharge"]))
            .with_entities(Observation.variable, Observation.observed_at, Observation.value)
            .all())
    per: dict[str, dict] = {}
    for var, ts, v in rows:
        per.setdefault(var, {})[ts.date()] = float(v)

    def _max(var):
        return round(max(per[var].values()), 2) if per.get(var) else None

    def _mean(var):
        return round(sum(per[var].values()) / len(per[var]), 3) if per.get(var) else None

    conditions = {
        "max_daily_rain_mm": _max("rain_mm"),
        "mean_soil_moisture": _mean("soil_moisture_0_7cm"),
        "max_discharge_m3s": _max("river_discharge"),
    }
    current = {
        "max_daily_rain_mm": None if pd.isna(feats.get("rain_1d")) else round(float(feats["rain_1d"]), 2),
        "mean_soil_moisture": None if pd.isna(feats.get("soil_moisture")) else round(float(feats["soil_moisture"]), 3),
        "max_discharge_m3s": None if pd.isna(feats.get("discharge")) else round(float(feats["discharge"]), 1),
    }
    return {
        "date": L.isoformat(),
        "days_ago": (now.date() - L).days,
        "kind": kind,
        "event_name": name,
        "glide": glide,
        "matched_report": matched,
        "conditions_during_flood": conditions,
        "conditions_now": current,
        "note": ("The model's flood-memory features (flood-level days in the past 30/90 "
                 "days and time since the last flood-level day) are derived from this "
                 "real history, so a recent flood raises the baseline probability while "
                 "it remains hydrologically relevant."),
    }


def _drivers(feats: dict, stats: pd.DataFrame) -> list[str]:
    checks = [
        ("rain_1d", "Recent rainfall is elevated"),
        ("rain_3d", "Multi-day accumulated rainfall is elevated"),
        ("rain_7d", "7-day accumulated rainfall is elevated"),
        ("soil_saturation", "Soil saturation is high"),
        ("discharge_ratio_mean", "River discharge is elevated for this season"),
    ]
    out = []
    for feat, label in checks:
        desc = _pctl_desc(feat, feats.get(feat, np.nan), stats)
        if desc:
            out.append(f"{label} ({desc})")
    return out or ["No indicator currently exceeds historical median levels"]


def _similarity(feats: dict, stats: pd.DataFrame) -> str:
    n = 0
    for feat in ["rain_1d", "rain_3d", "rain_7d", "soil_saturation", "discharge_ratio_mean"]:
        v = feats.get(feat, np.nan)
        if pd.notna(v) and feat in stats.index and v >= stats.loc[feat, "p75"]:
            n += 1
    if n >= 4:
        return "Strong similarity to historical flood-producing conditions"
    if n >= 2:
        return "Moderate similarity"
    return "Weak similarity"


def _confidence(freshness: dict) -> float:
    w = {"rain": 0.4, "soil_moisture": 0.3, "river_discharge": 0.3}
    score = sum(w[k] * (1.0 if freshness.get(k) == "fresh" else 0.5 if freshness.get(k) == "stale" else 0.0)
                for k in w)
    return round(score, 2)


def explain_district(db: Session, d: District) -> dict:
    """Full, honest explanation of the CURRENT prediction for one district:
    real feature values vs training-split statistics, plus per-feature
    contributions measured by ablation on the uncalibrated model output
    (the calibrated isotonic wrapper is monotonic; contributions are indicative,
    and this is stated)."""
    import numpy as _np

    c = _load()
    model = c["bundle"]["model"]
    card = c["card"]
    stats = c["stats"]
    now = datetime.utcnow()
    feats, freshness, cut = _features(db, d, now)

    missing_critical = any(pd.isna(feats[f]) for f in MISSING_CRITICAL if f in feats)
    X = _np.array([[float(feats[f]) if pd.notna(feats[f]) else _np.nan for f in FEATURES]],
                  dtype=float)

    # uncalibrated base model for ablation (inside the calibrated wrapper)
    base = model.calibrated_classifiers_[0].estimator
    p_full = float(base.predict_proba(X)[0][1])

    contributions = []
    for j, feat in enumerate(FEATURES):
        if _np.isnan(X[0][j]):
            continue
        Xab = X.copy()
        Xab[0][j] = _np.nan
        p_ab = float(base.predict_proba(Xab)[0][1])
        delta = p_full - p_ab
        if abs(delta) > 1e-4:
            contributions.append({"feature": feat, "delta": round(float(delta), 4)})
    contributions.sort(key=lambda x: -abs(x["delta"]))

    feature_rows = []
    for feat in FEATURES:
        label, unit = FEATURE_LABELS.get(feat, (feat, ""))
        v = feats.get(feat)
        row = {
            "feature": feat, "label": label, "unit": unit,
            "value": None if v is None or pd.isna(v) else float(v),
            "in_model": True,
        }
        if feat in stats.index and row["value"] is not None:
            row["hist_median"] = float(stats.loc[feat, "p50"])
            row["hist_p90"] = float(stats.loc[feat, "p90"])
            row["hist_p99"] = float(stats.loc[feat, "p99"])
        elif feat not in stats.index:
            row["in_model"] = True
        feature_rows.append(row)

    # --- pipeline signals (each computed from real values only) ---
    dis = _discharge_daily(db, d.id)
    rain_signal = _rain_signal(feats, stats)
    soil_signal = _soil_signal(feats, stats)
    dis_cond = discharge_condition(db, d, now, dis)
    ex30, ex90, days_since, _ = _hydro_memory(dis, now.date())

    # data quality: explicit, separate from the model estimate; uncertainty is
    # NOT quantified (we cannot scientifically do so) — stated instead.
    quality = "good" if all(freshness.get(k) == "fresh" for k in freshness) else (
        "fair" if all(freshness.get(k) in ("fresh", "stale") for k in freshness)
        else "poor — core measurements missing" if missing_critical else "fair")

    return {
        "district": {"id": d.id, "name": d.name, "state": d.state},
        "probability": None if missing_critical else p_full,
        "probability_meaning": (
            "Model-estimated probability that today's river discharge crosses the "
            "district's high-flow threshold (GloFAS 99th percentile). This is a "
            "hydrological threshold exceedance estimate — NOT a probability that an "
            "officially observed flood will occur."),
        "calibrated": False,
        "prediction_horizon": PREDICTION_HORIZON,
        "target_definition": TARGET_DEFINITIONS["y_hydro"],
        "missing_critical": missing_critical,
        "data_cut": cut.isoformat() if cut else None,
        "freshness": freshness,
        "data_quality": quality,
        "uncertainty_quantified": False,
        "uncertainty_note": ("Uncertainty is not quantified numerically; the model "
                             "score is not a calibrated real-world flood probability "
                             "and district averages can miss localized flash floods."),
        "signals": {
            "rain": rain_signal,
            "soil": soil_signal,
            "river": dis_cond,
            "recent_high_flow": {
                "exceedance_30d": ex30,
                "exceedance_90d": ex90,
                "days_since_last": days_since,
            },
        },
        "model_version": card["model_version"],
        "features": feature_rows,
        "contributions": contributions,
        "historical_analogues": historical_analogues(db, d, feats, now),
        "last_flood": _last_flood_context(db, d, feats, now),
        "method_notes": {
            "contributions": ("Model sensitivity: each value = model output with that "
                              "feature hidden, measured on the underlying gradient-boosting "
                              "model (indicative association, not causation; the published "
                              "score is the isotonic-calibrated version)."),
            "history": ("Percentiles compare today's value against ALL historical "
                        "days in the training split (2020-09..2023-08) for this "
                        "district — not only flood days."),
            "flood_memory": ("The model includes real high-flow memory features: reported "
                             "flood days (GDACS) and flood-level river-discharge days "
                             "(GloFAS P99 exceedance) in the past 30/90 days, and days "
                             "since the last such day. All are computed causally — only "
                             "information available at prediction time is used."),
            "meaning": ("The model estimates hydrological threshold exceedance, not an "
                        "officially confirmed flood. District-scale data can miss "
                        "localized flash floods; external data can be delayed."),
        },
    }


def compute_for_district(db: Session, d: District, run_id: datetime | None = None) -> RiskAssessment:
    c = _load()
    model, card, stats = c["bundle"]["model"], c["card"], c["stats"]
    thr = card["thresholds"]
    now = datetime.utcnow()
    feats, freshness, cut = _features(db, d, now)

    missing_critical = any(pd.isna(feats[f]) for f in MISSING_CRITICAL if f in feats)
    prob = None
    level = "DATA_UNAVAILABLE"
    drivers: list[str] = []
    similarity = None
    if not missing_critical:
        X = np.array([[float(feats[f]) if pd.notna(feats[f]) else np.nan for f in FEATURES]],
                     dtype=float)
        prob = float(model.predict_proba(X)[0][1])
        if prob >= thr["critical"]:
            level = "CRITICAL"
        elif prob >= thr["high"]:
            level = "HIGH"
        elif prob >= thr["moderate"]:
            level = "MODERATE"
        else:
            level = "LOW"
        drivers = _drivers(feats, stats)
        similarity = _similarity(feats, stats)
    else:
        drivers = []
        similarity = None

    prev = (db.query(RiskAssessment)
            .filter(RiskAssessment.district_id == d.id, RiskAssessment.probability.isnot(None))
            .order_by(RiskAssessment.computed_at.desc()).first())
    trend = "stable"
    if prev and prev.probability is not None and prob is not None:
        if prob - prev.probability > 0.1:
            trend = "increasing"
        elif prob - prev.probability < -0.1:
            trend = "decreasing"

    ra = RiskAssessment(
        district_id=d.id,
        model_version=card["model_version"],
        computed_at=run_id or now,
        probability=prob,
        risk_level=level,
        trend=trend,
        history_similarity=similarity,
        confidence=_confidence(freshness) if prob is not None else None,
        drivers=json.dumps(drivers),
        input_freshness=json.dumps(freshness),
        missing_critical=missing_critical,
        data_cut=cut,
    )
    db.merge(ra)
    return ra


def _insert_observations(db: Session, rows: list[dict]) -> None:
    """Idempotent bulk insert honoring the observation unique key."""
    from sqlalchemy.dialects import postgresql, sqlite

    if not rows:
        return
    table = Observation.__table__
    for i in range(0, len(rows), 20000):
        part = rows[i:i + 20000]
        if db.bind.dialect.name == "postgresql":
            stmt = postgresql.insert(table).on_conflict_do_nothing()
        else:
            stmt = sqlite.insert(table).on_conflict_do_nothing()
        db.execute(stmt, part)
    db.commit()


def store_currents(db: Session, districts: list[District], currents: list[dict]) -> None:
    """Persist real current/hourly data from Open-Meteo forecast responses.
    Hourly precipitation is aggregated to DAILY sums and soil moisture to daily
    last-value, matching the daily cadence the model was trained on. The raw
    current 'now' values are stored under *_now variables for display."""
    from collections import defaultdict

    now = datetime.utcnow()
    rows: list[dict] = []
    for d, cur in zip(districts, currents):
        if not cur:
            continue  # chunk skipped due to rate limit; retried next cycle
        c = cur.get("current") or {}
        hourly = cur.get("hourly") or {}
        times = hourly.get("time") or []
        rains = hourly.get("precipitation") or []
        sms = hourly.get("soil_moisture_0_to_7cm") or []

        def mk(ts, var, val, unit):
            if val is None or ts is None:
                return
            rows.append({
                "district_id": d.id, "source": "open-meteo-forecast", "variable": var,
                "observed_at": ts, "fetched_at": now, "value": float(val),
                "unit": unit, "quality": "ok"})

        t0 = datetime.fromisoformat(c["time"]) if c.get("time") else now
        mk(t0, "rain_now_mm", c.get("precipitation"), "mm")
        mk(t0, "soil_moisture_0_7cm", c.get("soil_moisture_0_to_7cm"), "m3/m3")

        rain_by_day: dict = defaultdict(float)
        sm_by_day: dict = {}
        for ts_s, rv, sv in zip(times, rains, sms):
            ts = datetime.fromisoformat(ts_s)
            day = ts.date()
            if rv is not None:
                rain_by_day[day] += float(rv)
            if sv is not None:
                sm_by_day[day] = float(sv)  # last non-null of the day
        for day, total in rain_by_day.items():
            mk(datetime.combine(day, datetime.min.time()), "rain_mm", round(total, 2), "mm")
        for day, v in sm_by_day.items():
            mk(datetime.combine(day, datetime.min.time()), "soil_moisture_0_7cm", v, "m3/m3")
    _insert_observations(db, rows)


def store_discharge_currents(db: Session, districts: list[District], entries: list[dict]) -> None:
    """Store latest real daily GloFAS discharge values (day keyed at 00:00,
    consistent with the historical backfill)."""
    now = datetime.utcnow()
    rows: list[dict] = []
    for d, entry in zip(districts, entries):
        if not entry:
            continue
        daily = entry.get("daily") or {}
        for t, v in zip(daily.get("time") or [], daily.get("river_discharge") or []):
            if v is None:
                continue
            rows.append({
                "district_id": d.id, "source": "open-meteo-glofas",
                "variable": "river_discharge", "observed_at": datetime.fromisoformat(t),
                "fetched_at": now, "value": float(v), "unit": "m3/s", "quality": "ok"})
    _insert_observations(db, rows)


def store_archive_fallback(db: Session, districts: list[District], entries: list[dict]) -> None:
    """Rain/soil rows from the ERA5 archive for districts whose forecast chunk
    was throttled this cycle. The archive endpoint lives on a separate quota
    that stays reachable when api.open-meteo.com rate-limits a shared egress
    IP, and it serves days up to today — so features keep real inputs instead
    of going NaN. Same source label and day-keying as the historical backfill."""
    now = datetime.utcnow()
    rows: list[dict] = []
    unit_map = {"rain_mm": "mm", "soil_moisture_0_7cm": "m3/m3"}
    for d, entry in zip(districts, entries):
        if not entry:
            continue
        daily = entry.get("daily") or {}
        times = daily.get("time") or []
        rains = daily.get("precipitation_sum") or []
        soils = daily.get("soil_moisture_0_to_7cm_mean") or []
        for t, r, s in zip(times, rains, soils):
            ts = datetime.combine(datetime.fromisoformat(t).date(), datetime.min.time())
            for var, v in (("rain_mm", r), ("soil_moisture_0_7cm", s)):
                if v is None:
                    continue
                rows.append({
                    "district_id": d.id, "source": "open-meteo-era5", "variable": var,
                    "observed_at": ts, "fetched_at": now, "value": float(v),
                    "unit": unit_map[var], "quality": "ok"})
    _insert_observations(db, rows)


async def refresh_all_risk(db: Session) -> int:
    """Fetch latest real data for all districts, store, then score each district."""
    from datetime import date as _date

    from ..data_sources.open_meteo import fetch_archive_daily, fetch_current, fetch_river_discharge

    districts = db.query(District).all()
    coords = [(d.lat, d.lon) for d in districts]

    currents = await fetch_current(coords)
    store_currents(db, districts, currents)

    today = _date.today()
    # Forecast chunks that stayed throttled even after retries: refill those
    # districts' rain/soil from the ERA5 archive (days up to today) so the
    # scoring below has real inputs rather than NaN critical features.
    missed = [(d, c) for d, c, cur in zip(districts, coords, currents) if not cur]
    if missed:
        try:
            entries = await fetch_archive_daily(
                [c for _, c in missed], start=today - timedelta(days=9), end=today)
            store_archive_fallback(db, [d for d, _ in missed], entries)
            print(f"archive fallback: rain/soil refilled for {len(missed)} throttled districts")
        except Exception as e:  # noqa: BLE001
            print(f"archive fallback failed this cycle: {e}")

    try:
        discharge = await fetch_river_discharge(
            coords, start=today - timedelta(days=2), end=today + timedelta(days=1))
        store_discharge_currents(db, districts, discharge)
    except Exception as e:  # noqa: BLE001
        print(f"discharge refresh failed this cycle: {e}")

    run_id = datetime.utcnow()
    n = 0
    for d in districts:
        try:
            compute_for_district(db, d, run_id=run_id)
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"risk compute failed for {d.name}: {e}")
    db.commit()
    return n
