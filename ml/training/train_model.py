"""Train and evaluate the flash-flood model on REAL data (spec §8, §9, §21, §22).

Protocol (documented, reproducible):
  - Time-based splits: TRAIN 2020-09..2023-08 | VAL 2023-09..2024-08 | TEST 2024-09..2025-08
  - Two real targets are trained and compared (label sparsity of reported events
    is documented — DATA_SOURCE_MATRIX.md §4):
      y       : GDACS flood report for the district within [t, t+3]
      y_hydro : GloFAS discharge >= causal 5y rolling P99 (hydrological threshold)
  - Candidates per target: LogisticRegression (baseline, balanced) and
    HistGradientBoostingClassifier (sample_weight balanced). Algorithm AND target
    are SELECTED ON VALIDATION PR-AUC — not assumed.
  - Class imbalance handled via class/sample weights; no synthetic events.
  - Probabilities calibrated with isotonic regression fit on VALIDATION (cv='prefit').
  - Decision thresholds derived ONLY on validation, method stored in the card:
      MODERATE  : smallest p with >= 2x lift over validation prevalence
      HIGH      : threshold maximizing F2 on validation (recall-weighted)
      CRITICAL  : smallest p with precision >= 0.5, else max-precision with recall >= 0.25
  - Final metrics reported on untouched TEST split.

Outputs: ml/models/model.joblib, data/processed/model_card.json,
         data/processed/feature_importance.json, data/processed/calibration.json
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score, brier_score_loss, confusion_matrix, f1_score, fbeta_score,
    precision_recall_curve, precision_score, r2_score, recall_score, roc_auc_score,
    roc_curve,
)
from sklearn.calibration import calibration_curve
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

BASE = Path(__file__).resolve().parents[2]
PROCESSED = BASE / "data" / "processed"
MODELS_DIR = BASE / "ml" / "models"

SEED = 42


def _metrics_block(y_true: np.ndarray, p: np.ndarray) -> dict:
    pred = (p >= 0.5).astype(int)
    try:
        roc = float(roc_auc_score(y_true, p))
    except ValueError:
        roc = None
    return {
        "n": int(len(y_true)),
        "positives": int(y_true.sum()),
        "accuracy_at_0.5": float((pred == y_true).mean()),
        "precision_at_0.5": float(precision_score(y_true, pred, zero_division=0)),
        "recall_at_0.5": float(recall_score(y_true, pred, zero_division=0)),
        "f1_at_0.5": float(f1_score(y_true, pred, zero_division=0)),
        "roc_auc": roc,
        "pr_auc": float(average_precision_score(y_true, p)),
        "brier": float(brier_score_loss(y_true, p)),
    }


def _confusion(y: np.ndarray, p: np.ndarray, thr: float) -> dict:
    pred = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {"threshold": thr, "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}


def _derive_thresholds(y_val: np.ndarray, p_val: np.ndarray) -> dict:
    """Thresholds derived ONLY on validation; method documented in output."""
    prevalence = float(y_val.mean())
    order = np.argsort(-p_val)
    p_sorted, y_sorted = p_val[order], y_val[order]

    # MODERATE: >= 2x lift over climatology
    mod_candidates = p_val[p_val >= 2 * prevalence]
    moderate = float(np.min(mod_candidates)) if len(mod_candidates) else 2 * prevalence

    # HIGH: maximize F2 (recall-weighted) on validation
    prec, rec, thr = precision_recall_curve(y_val, p_val)
    f2 = (5 * prec * rec) / np.maximum(4 * prec + rec, 1e-9)
    high = float(thr[int(np.nanargmax(f2[:-1]))]) if len(thr) else moderate

    # CRITICAL: precision >= 0.5 preferred, else best-precision with recall >= 0.25
    crit = None
    for i in range(len(prec) - 1, -1, -1):
        if prec[i] >= 0.5 and rec[i] >= 0.1:
            crit = float(thr[min(i, len(thr) - 1)])
            break
    if crit is None:
        mask = rec[:-1] >= 0.25
        if mask.any():
            i = int(np.nanargmax(np.where(mask, prec[:-1], -1)))
            crit = float(thr[i])
        else:
            crit = high
    return {
        "moderate": round(min(moderate, high), 4),
        "high": round(high, 4),
        "critical": round(max(crit, high), 4),
        "method": {
            "moderate": "min p with >=2x lift over validation prevalence",
            "high": "validation threshold maximizing F2 (beta=2)",
            "critical": "min validation p with precision>=0.5, else max-precision with recall>=0.25",
        },
        "validation_prevalence": round(prevalence, 6),
    }


def _fit_eval_target(df: pd.DataFrame, features: list[str], target: str):
    """Fit both candidate models for one target; return fitted models + val probs."""
    train = df[df["split"] == "train"]
    val = df[df["split"] == "val"]
    Xtr, ytr = train[features].to_numpy(dtype=float), train[target].to_numpy()
    Xva = val[features].to_numpy(dtype=float)

    pos_weight = max(1.0, float((ytr == 0).sum() / max(1, (ytr == 1).sum())))
    logit = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=SEED)),
    ])
    logit.fit(Xtr, ytr)

    hgb = HistGradientBoostingClassifier(
        max_iter=400, learning_rate=0.06, max_leaf_nodes=31,
        l2_regularization=1.0, random_state=SEED,
    )
    hgb.fit(Xtr, ytr, sample_weight=np.where(ytr == 1, pos_weight, 1.0))

    return {
        "logistic_regression": (logit, logit.predict_proba(Xva)[:, 1]),
        "hist_gradient_boosting": (hgb, hgb.predict_proba(Xva)[:, 1]),
    }


def main() -> None:
    df = pd.read_pickle(PROCESSED / "dataset.pkl")
    meta = json.loads((PROCESSED / "dataset_meta.json").read_text())
    features = meta["features"]
    test = df[df["split"] == "test"]
    Xte = test[features].to_numpy(dtype=float)

    # ---- fit candidates for BOTH targets; select target+algorithm on validation ----
    fitted = {"y": _fit_eval_target(df, features, "y"),
              "y_hydro": _fit_eval_target(df, features, "y_hydro")}
    val = df[df["split"] == "val"]
    val_sel: dict[str, dict] = {}
    for tgt, models in fitted.items():
        yva = val[tgt].to_numpy()
        for algo, (_, pva) in models.items():
            val_sel[f"{tgt}|{algo}"] = _metrics_block(yva, pva)
    best_key = max(val_sel, key=lambda k: val_sel[k]["pr_auc"] or 0)
    best_target, best_algo = best_key.split("|")
    print("validation selection:")
    for k, v in val_sel.items():
        print(f"  {k}: PR-AUC {v['pr_auc']:.4f}")
    print(f"-> selected {best_key}")

    base_model, _ = fitted[best_target][best_algo]
    yva = val[best_target].to_numpy()
    yte = test[best_target].to_numpy()

    Xva = val[features].to_numpy(dtype=float)
    # ---- calibration on validation (cv='prefit') ----
    from sklearn.calibration import CalibratedClassifierCV
    calib = CalibratedClassifierCV(base_model, method="isotonic", cv="prefit")
    calib.fit(Xva, yva)

    pva = calib.predict_proba(Xva)[:, 1]
    pte = calib.predict_proba(Xte)[:, 1]

    thresholds = _derive_thresholds(yva, pva)
    thr_test = thresholds["high"]  # operating point for headline recall metrics

    test_metrics = _metrics_block(yte, pte)
    test_metrics.update({
        "confusion_at_high_threshold": _confusion(yte, pte, thresholds["high"]),
        "confusion_at_critical_threshold": _confusion(yte, pte, thresholds["critical"]),
    })

    # ---- explainability: permutation importance on TEST (real, model-derived) ----
    perm = permutation_importance(calib, Xte, yte, scoring="average_precision",
                                  n_repeats=5, random_state=SEED)
    imp = sorted(zip(features, perm.importances_mean.tolist()),
                 key=lambda kv: -abs(kv[1]))
    feature_importance = [{"feature": f, "importance": round(float(v), 6)} for f, v in imp]

    # ---- calibration curve (test) ----
    frac_pos, mean_pred = calibration_curve(yte, pte, n_bins=8, strategy="quantile")
    calibration = {
        "prob_true": [round(float(x), 4) for x in frac_pos],
        "prob_pred": [round(float(x), 4) for x in mean_pred],
        "brier_test": test_metrics["brier"],
    }

    # ---- persist ----
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": calib, "features": features,
                 "selected_algorithm": best_algo, "selected_target": best_target},
                MODELS_DIR / "model.joblib")

    periods = {
        "train": "2020-09-01..2023-08-31",
        "validation": "2023-09-01..2024-08-31",
        "test": "2024-09-01..2025-08-31",
    }
    import datetime as _dt

    model_version = (f"v1-{best_target}-{'hgb' if 'hist' in best_algo else 'logit'}-"
                     f"{_dt.date.today().isoformat().replace('-', '')}")  # e.g. v1-y_hydro-hgb-20260925
    card = {
        "model_version": model_version,
        "selected_algorithm": best_algo,
        "selected_target": best_target,
        "target_definitions": {
            "y": meta["label_definition"],
            "y_hydro": meta["label_definition_hydro"],
        },
        "validation_selection": val_sel,
        "test_metrics": test_metrics,
        "thresholds": thresholds,
        "periods": periods,
        "features": features,
        "class_handling": "class/sample weights; isotonic calibration on validation",
        "random_seed": SEED,
        "n_rows": meta["n_rows"],
        "positives": meta["positives"],
        "positives_hydro": meta["positives_hydro"],
    }
    (PROCESSED / "model_card.json").write_text(json.dumps(card, indent=2))
    (PROCESSED / "feature_importance.json").write_text(json.dumps(feature_importance, indent=2))
    (PROCESSED / "calibration.json").write_text(json.dumps(calibration, indent=2))

    print(json.dumps({k: card[k] for k in
                      ["model_version", "selected_algorithm", "test_metrics", "thresholds"]},
                     indent=2)[:2000])


if __name__ == "__main__":
    main()
