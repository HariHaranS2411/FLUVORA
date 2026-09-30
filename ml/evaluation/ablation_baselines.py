"""Feature-group ablation & hydrological-persistence baseline (honest evaluation).

Question answered (report only, no fabrication):
  Does the ML model add predictive value beyond simple hydrological persistence?
  Discharge is persistent, so a model can look good by learning
  "today's discharge predicts tomorrow's discharge".

Feature groups, all trained with the SAME protocol as ml/training/train_model.py
(time-based splits, HistGradientBoosting, class weighting, isotonic calibration
on validation, metrics on the untouched TEST split):

  A  discharge_only          current discharge + seasonal discharge ratios
                             (the persistence baseline dressed as a model)
  B  rain_soil_only          rainfall + soil features + terrain + month
                             (no discharge at all)
  C  rain_soil_discharge     B + current discharge/ratios (no flood memory)
  D  full                    every feature used by the deployed model
  persistence_baseline       no ML: predict y_hydro(t+1) := 1[y_hydro(t)]
                             measured directly, no training

Output: data/processed/ablation_report.json + console table.
This report is INFORMATIONAL — it does not change the deployed model.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (
    average_precision_score, brier_score_loss, roc_auc_score,
)

BASE = Path(__file__).resolve().parents[2]
PROCESSED = BASE / "data" / "processed"
SEED = 42

GROUPS: dict[str, list[str]] = {
    "A_discharge_only": [
        "discharge", "discharge_ratio_mean", "discharge_ratio_median",
        "elevation_m", "slope_deg", "month",
    ],
    "B_rain_soil_only": [
        "rain_1d", "rain_2d", "rain_3d", "rain_5d", "rain_7d",
        "rainfall_change", "rainfall_acceleration", "recent_vs_previous",
        "api_7", "rain_hours_3d",
        "soil_moisture", "soil_saturation", "soil_moisture_change",
        "elevation_m", "slope_deg", "month",
    ],
    "C_rain_soil_discharge": [
        "rain_1d", "rain_2d", "rain_3d", "rain_5d", "rain_7d",
        "rainfall_change", "rainfall_acceleration", "recent_vs_previous",
        "api_7", "rain_hours_3d",
        "soil_moisture", "soil_saturation", "soil_moisture_change",
        "discharge", "discharge_ratio_mean", "discharge_ratio_median",
        "elevation_m", "slope_deg", "month",
    ],
}


def _metrics(y: np.ndarray, p: np.ndarray) -> dict:
    out: dict = {"n": int(len(y)), "positives": int(y.sum())}
    try:
        out["roc_auc"] = round(float(roc_auc_score(y, p)), 4)
    except ValueError:
        out["roc_auc"] = None
    out["pr_auc"] = round(float(average_precision_score(y, p)), 4)
    out["brier"] = round(float(brier_score_loss(y, p)), 6)
    return out


def _fit_eval(df: pd.DataFrame, features: list[str], target: str) -> dict:
    train = df[df["split"] == "train"]
    val = df[df["split"] == "val"]
    test = df[df["split"] == "test"]
    Xtr = train[features].to_numpy(dtype=float)
    ytr = train[target].to_numpy()
    pos_weight = max(1.0, float((ytr == 0).sum() / max(1, (ytr == 1).sum())))

    model = HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.06, max_leaf_nodes=31,
        l2_regularization=1.0, random_state=SEED,
    )
    model.fit(Xtr, ytr, sample_weight=np.where(ytr == 1, pos_weight, 1.0))

    Xva = val[features].to_numpy(dtype=float)
    yva = val[target].to_numpy()
    Xte = test[features].to_numpy(dtype=float)
    yte = test[target].to_numpy()

    # isotonic calibration on validation (same as deployed protocol)
    from sklearn.calibration import CalibratedClassifierCV
    calib = CalibratedClassifierCV(model, method="isotonic", cv="prefit")
    calib.fit(Xva, yva)

    return {
        "validation": _metrics(yva, calib.predict_proba(Xva)[:, 1]),
        "test": _metrics(yte, calib.predict_proba(Xte)[:, 1]),
    }


def main() -> None:
    df = pd.read_pickle(PROCESSED / "dataset.pkl")
    meta = json.loads((PROCESSED / "dataset_meta.json").read_text())
    all_features = meta["features"]
    target = "y_hydro"  # the deployed model's actual target

    report: dict = {
        "question": ("Does the gradient-boosting model add predictive value beyond "
                     "simple hydrological persistence of discharge?"),
        "target": target,
        "target_definition": meta["label_definition_hydro"],
        "protocol": ("time-based splits (train 2020-09..2023-08, val 2023-09..2024-08, "
                     "test 2024-09..2025-08), HistGradientBoosting, class weighting, "
                     "isotonic calibration on validation, metrics on untouched test split"),
        "models": {},
    }

    for name, feats in GROUPS.items():
        feats = [f for f in feats if f in all_features]
        print(f"training {name} ({len(feats)} features)...")
        report["models"][name] = {"features": feats, **_fit_eval(df, feats, target)}

    print("training D_full (all features)...")
    report["models"]["D_full"] = {"features": all_features, **_fit_eval(df, all_features, target)}

    # --- Persistence baseline: no ML. y_hydro is knowable at t, predict t+1. ---
    print("evaluating persistence baseline...")
    d = df.sort_values(["district_id", "date"]).copy()
    d["y_prev"] = d.groupby("district_id", sort=False)[target].shift(1)
    val = d[(d["split"] == "val") & d["y_prev"].notna()]
    test = d[(d["split"] == "test") & d["y_prev"].notna()]

    def persistence_metrics(part: pd.DataFrame) -> dict:
        y = part[target].to_numpy()
        p = part["y_prev"].to_numpy()  # binary "forecast" = yesterday's state
        m = _metrics(y, p)
        # recall/precision of the deterministic rule
        pred = (p >= 1).astype(int)
        tp = int(((pred == 1) & (y == 1)).sum())
        fp = int(((pred == 1) & (y == 0)).sum())
        fn = int(((pred == 0) & (y == 1)).sum())
        m["rule_precision"] = round(tp / (tp + fp), 4) if tp + fp else None
        m["rule_recall"] = round(tp / (tp + fn), 4) if tp + fn else None
        return m

    report["models"]["persistence_baseline_yesterday_state"] = {
        "features": ["y_hydro(t-1)"],
        "validation": persistence_metrics(val),
        "test": persistence_metrics(test),
        "note": ("no ML: predicts the district stays in high-flow state; "
                 "pure discharge persistence"),
    }

    # --- Honest interpretation, computed from the numbers above ---
    def pr(m: dict) -> float:
        return m["test"]["pr_auc"] if m.get("test") else 0.0

    full_pr = pr(report["models"]["D_full"])
    dis_pr = pr(report["models"]["A_discharge_only"])
    bs_pr = pr(report["models"]["B_rain_soil_only"])
    pers_recall = report["models"]["persistence_baseline_yesterday_state"]["test"].get("rule_recall")

    report["interpretation"] = {
        "full_vs_discharge_only_pr_auc_test": [full_pr, dis_pr],
        "full_vs_rain_soil_only_pr_auc_test": [full_pr, bs_pr],
        "persistence_rule_recall_test": pers_recall,
        "finding": (
            "Discharge persistence alone (model A) is a strong predictor of tomorrow's "
            "high-flow state. Compare A vs D on PR-AUC: the gap is the model's value beyond "
            "persistence. Rain+soil without discharge (model B) shows whether rainfall "
            "carries independent signal. These numbers are computed, not assumed."
        ),
        "caution": (
            "Even where D > A, part of the model's skill is hydrological persistence, not "
            "genuine flood anticipation. The UI therefore labels the output as "
            "'high-flow / hydrological threshold exceedance' estimates, never as confirmed "
            "flood probabilities."
        ),
    }

    (PROCESSED / "ablation_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "models"}, indent=2))
    for name, m in report["models"].items():
        t = m.get("test", {})
        print(f"{name:42s} test PR-AUC {t.get('pr_auc')}  ROC-AUC {t.get('roc_auc')}")


if __name__ == "__main__":
    main()
