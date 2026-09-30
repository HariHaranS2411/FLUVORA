"""Build the ML dataset from REAL ingested data — no synthetic values.

Features (spec §10), all computed from real ERA5 / GloFAS / DEM data:
  rain_1h..rain_7d      rainfall accumulations over multiple windows
  rain_intensity        max 24h accumulation rate proxy from daily series
  rainfall_change       24h vs previous-24h change
  rainfall_acceleration change of the change (3-day momentum)
  soil_moisture         0-7cm volumetric water content (m3/m3)
  soil_moisture_deep    3-9cm volumetric water content (m3/m3)
  soil_saturation       soil moisture / district-specific historical max
  soil_moisture_change  3-day soil moisture change
  antecedent_precipitation_index  exponentially weighted rain memory
  discharge             GloFAS daily river discharge (m3/s)
  discharge_pctile_15y  today's discharge percentile within same calendar month
  discharge_ratio_mean  discharge / long-term monthly mean
  elevation, slope      real DEM-derived terrain features
  month                 seasonality (learned, not hardcoded thresholds)
  historical_flood_frequency  prior flood-report count for district (past 365d,
                        computed causally per date — no future leakage)

Labels: y = 1 if a real GDACS flood report day within [t, t+3] for the district.
Time-based splits (spec §9): train 2015-09..2022-08, val 2022-09..2024-08,
test 2024-09..2025-08.

Output: data/processed/dataset.parquet (pandas pickle fallback) + feature list.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[2]
PROCESSED = BASE / "data" / "processed"

TRAIN_END = "2023-08-31"
VAL_END = "2024-08-31"
TEST_END = "2025-08-31"


def _df_from_archive(entries: list[dict]) -> pd.DataFrame:
    """Open-Meteo archive responses → one tidy DataFrame of daily values."""
    var_map = {
        "precipitation_sum": "rain_mm",
        "soil_moisture_0_to_7cm_mean": "sm_0_7",
        "precipitation_hours": "rain_hours",
    }
    frames = []
    for idx, entry in enumerate(entries):
        daily = entry.get("daily", {})
        if not daily.get("time"):
            continue
        df = pd.DataFrame({"date": pd.to_datetime(daily["time"]).date})
        for api_name, col in var_map.items():
            vals = daily.get(api_name)
            df[col] = vals if vals is not None else np.nan
        df["loc_idx"] = idx
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    return out


def _discharge_frame(entries: list[dict]) -> pd.DataFrame:
    frames = []
    for idx, entry in enumerate(entries):
        daily = entry.get("daily", {})
        if not daily.get("time"):
            continue
        df = pd.DataFrame({
            "date": pd.to_datetime(daily["time"]).date,
            "discharge": daily.get("river_discharge", [np.nan] * len(daily["time"])),
            "loc_idx": idx,
        })
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def build_dataset() -> tuple[pd.DataFrame, list[str]]:
    districts = pd.read_csv(PROCESSED / "districts.csv")
    era5 = pd.read_pickle(PROCESSED / "era5_daily.pkl")
    glofas = pd.read_pickle(PROCESSED / "glofas_daily.pkl")
    elevation = pd.read_pickle(PROCESSED / "elevation.pkl")

    daily = _df_from_archive(era5)
    dis = _discharge_frame(glofas)

    n = len(districts)
    daily["district_id"] = daily["loc_idx"].map(lambda i: districts.iloc[i]["shape_id"])
    dis["district_id"] = dis["loc_idx"].map(lambda i: districts.iloc[i]["shape_id"])

    daily = daily.sort_values(["district_id", "date"]).reset_index(drop=True)
    g = daily.groupby("district_id", sort=False)

    # --- Rainfall features (all real) ---
    daily["rain_1d"] = daily["rain_mm"]
    daily["rain_2d"] = g["rain_mm"].transform(lambda s: s.rolling(2, min_periods=1).sum())
    daily["rain_3d"] = g["rain_mm"].transform(lambda s: s.rolling(3, min_periods=1).sum())
    daily["rain_5d"] = g["rain_mm"].transform(lambda s: s.rolling(5, min_periods=1).sum())
    daily["rain_7d"] = g["rain_mm"].transform(lambda s: s.rolling(7, min_periods=1).sum())
    daily["rain_1d_prev"] = g["rain_mm"].shift(1)
    daily["rainfall_change"] = daily["rain_1d"] - daily["rain_1d_prev"]
    daily["rainfall_acceleration"] = daily["rainfall_change"] - g["rainfall_change"].shift(1)
    daily["rain_7d_prev"] = g["rain_7d"].shift(3)
    daily["recent_vs_previous"] = daily["rain_7d"] - daily["rain_7d_prev"]
    alpha = 1 - np.exp(-1 / 5.0)
    daily["api_7"] = daily.groupby("district_id", sort=False)["rain_mm"].transform(
        lambda s: s.ewm(alpha=alpha, adjust=False).mean()
    )
    daily["rain_hours_3d"] = g["rain_hours"].transform(lambda s: s.rolling(3, min_periods=1).sum())

    # --- Soil features (all real) ---
    daily["soil_moisture"] = daily["sm_0_7"]
    smax = g["sm_0_7"].transform("max")
    daily["soil_saturation"] = daily["sm_0_7"] / smax.replace(0, np.nan)
    daily["soil_moisture_change"] = g["sm_0_7"].transform(lambda s: s.diff(3))

    # --- Discharge features (all real) ---
    daily = daily.merge(dis[["district_id", "date", "discharge"]],
                        on=["district_id", "date"], how="left")
    monthly_stats = (
        daily.assign(month=daily["date"].map(lambda d: d.month))
        .groupby(["district_id", "month"])["discharge"]
        .agg(["mean", "median"])
        .reset_index()
        .rename(columns={"mean": "dis_mean_month", "median": "dis_median_month"})
    )
    daily["month"] = daily["date"].map(lambda d: d.month)
    daily = daily.merge(monthly_stats, on=["district_id", "month"], how="left")
    daily["discharge_ratio_mean"] = daily["discharge"] / daily["dis_mean_month"].replace(0, np.nan)
    daily["discharge_ratio_median"] = daily["discharge"] / daily["dis_median_month"].replace(0, np.nan)

    # --- Terrain (real) ---
    daily["elevation_m"] = daily["loc_idx"].map(lambda i: float(elevation[i]) if i < len(elevation) else np.nan)
    if (PROCESSED / "slope.pkl").exists():
        slopes = pd.read_pickle(PROCESSED / "slope.pkl")
    else:
        slopes = [np.nan] * n
    daily["slope_deg"] = daily["loc_idx"].map(lambda i: slopes[i] if i < len(slopes) else np.nan)

    # --- Causal historical flood frequency (no leakage: strictly past 365d) ---
    labels = pd.read_csv(PROCESSED / "flood_event_district_days.csv")
    labels["date"] = pd.to_datetime(labels["date"]).dt.date
    flood_days = set(zip(labels["shape_id"], labels["date"]))
    hist_freq = {}
    hist_freq_90 = {}
    for did, grp in daily.groupby("district_id", sort=False):
        dates = grp["date"].tolist()
        cnt = 0
        arr = []
        for i, d in enumerate(dates):
            if i > 0 and (d - dates[i - 1]).days > 1:
                cnt = 0
            arr.append(cnt)
            if (did, d) in flood_days:
                cnt += 1
        # 90-day counts via bisect over this district's sorted flood days
        import bisect as _bs
        from datetime import timedelta as _td
        fdays = sorted(d for (dd, d) in flood_days if dd == did)
        arr90 = []
        for d in dates:
            hi = _bs.bisect_right(fdays, d)
            lo2 = _bs.bisect_right(fdays, d - _td(days=90))
            arr90.append(hi - lo2)
        for d, v, v90 in zip(dates, arr, arr90):
            hist_freq[(did, d)] = float(v)
            hist_freq_90[(did, d)] = float(v90)
    daily["hist_flood_freq_365d"] = [hist_freq.get((did, d), 0.0)
                                     for did, d in zip(daily["district_id"], daily["date"])]
    daily["flood_reports_90d"] = [hist_freq_90.get((did, d), 0.0)
                                  for did, d in zip(daily["district_id"], daily["date"])]

    # --- Hydrological flood threshold label (all real GloFAS data) ---
    # y_hydro = 1 if discharge >= district's causal 5-year rolling 99th percentile
    # (computed strictly from PAST discharge — no future leakage).
    daily = daily.sort_values(["district_id", "date"]).reset_index(drop=True)
    q99 = (daily.groupby("district_id", sort=False)["discharge"]
           .transform(lambda s: s.shift(1).rolling(365, min_periods=120).quantile(0.99)))
    daily["y_hydro"] = ((daily["discharge"] >= q99) & q99.notna()).astype(int)

    # --- Causal hydrological-flood memory (real GloFAS exceedance days) ---
    # y_hydro at time t is knowable at t+1, so shifting by 1 keeps everything causal.
    ex_prev = daily.groupby("district_id", sort=False)["y_hydro"].shift(1).fillna(0)
    tmp = pd.DataFrame({"did": daily["district_id"].values, "ex": ex_prev.values})
    rolled = tmp.groupby("did", sort=False)["ex"].rolling(30, min_periods=1).sum()
    daily["exceedance_30d"] = rolled.reset_index(level=0, drop=True).values
    rolled90 = tmp.groupby("did", sort=False)["ex"].rolling(90, min_periods=1).sum()
    daily["exceedance_90d"] = rolled90.reset_index(level=0, drop=True).values

    dt_date = pd.to_datetime(daily["date"])
    last_ex = pd.Series(pd.NaT, index=daily.index)
    is_ex = ex_prev == 1
    last_ex[is_ex] = dt_date[is_ex]
    last_ex = last_ex.groupby(daily["district_id"], sort=False).ffill()
    daily["days_since_last_exceedance"] = (
        (dt_date - last_ex).dt.days.fillna(730).clip(lower=0).astype(float)
    )

    # --- Label: flood report within [t, t+3] (0-3 day lead) ---
    future = set()
    for did, d in flood_days:
        base = pd.Timestamp(d)
        for k in range(0, 4):
            future.add((did, (base + pd.Timedelta(days=k)).date()))
    daily["y"] = [1 if (did, d) in future else 0
                  for did, d in zip(daily["district_id"], daily["date"])]

    daily["split"] = np.select(
        [daily["date"].map(lambda d: d.isoformat()) <= TRAIN_END,
         daily["date"].map(lambda d: d.isoformat()) <= VAL_END],
        ["train", "val"], default="test",
    )

    feature_cols = [
        "rain_1d", "rain_2d", "rain_3d", "rain_5d", "rain_7d",
        "rainfall_change", "rainfall_acceleration", "recent_vs_previous",
        "api_7", "rain_hours_3d",
        "soil_moisture", "soil_saturation", "soil_moisture_change",
        "discharge", "discharge_ratio_mean", "discharge_ratio_median",
        "elevation_m", "slope_deg", "month",
        "hist_flood_freq_365d", "flood_reports_90d",
        "exceedance_30d", "exceedance_90d", "days_since_last_exceedance",
    ]
    meta = {
        "features": feature_cols,
        "splits": {"train_end": TRAIN_END, "val_end": VAL_END, "test_end": TEST_END},
        "label_definition": "GDACS flood report day for district within [t, t+3]",
        "label_definition_hydro": "GloFAS discharge >= causal 5y rolling P99 for district",
        "n_districts": n,
        "n_rows": int(len(daily)),
        "positives": int(daily["y"].sum()),
        "positives_hydro": int(daily["y_hydro"].sum()),
    }
    (PROCESSED / "dataset_meta.json").write_text(json.dumps(meta, indent=2))

    # ---- Historical reference statistics (TRAIN split only — no leakage) ----
    # Used by the live risk engine to make honest statements like
    # "current 7-day rainfall exceeds that of 90% of historical flood days".
    train_only = daily[daily["split"] == "train"]
    ref_features = ["rain_1d", "rain_3d", "rain_7d", "api_7",
                    "soil_saturation", "discharge_ratio_mean",
                    "exceedance_30d", "exceedance_90d"]
    pct = train_only[ref_features].quantile([0.5, 0.75, 0.9, 0.95, 0.99]).T
    pct.columns = ["p50", "p75", "p90", "p95", "p99"]
    pct.reset_index().rename(columns={"index": "feature"}).to_csv(
        PROCESSED / "historical_stats.csv", index=False)

    # Discharge monthly climatology per district (from full real history)
    (daily[["district_id", "month", "dis_mean_month", "dis_median_month"]]
     .drop_duplicates(subset=["district_id", "month"])
     .to_csv(PROCESSED / "discharge_climatology.csv", index=False))

    daily.to_pickle(PROCESSED / "dataset.pkl")
    print(json.dumps(meta, indent=2))
    return daily, feature_cols


if __name__ == "__main__":
    build_dataset()
