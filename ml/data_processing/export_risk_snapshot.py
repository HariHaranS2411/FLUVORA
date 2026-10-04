"""Export the most recent usable risk assessment per district from the local
full-history database into data/processed/last_good_risk.json.

Why: Render's free tier runs on an ephemeral filesystem — every spin-up starts
from an empty database — and Open-Meteo's shared-egress-IP quota is frequently
exhausted there, so a fresh boot can serve 735 "no data" districts for hours.
This snapshot carries REAL model outputs (with their true computed_at) into
git; app/services/risk_snapshot.py seeds them at bootstrap so the deployed app
shows the last-known-good estimate, honestly dated, instead of nothing.

Re-run this script after notable local data refreshes to refresh the shipped
baseline (the backend refuses snapshots older than risk_snapshot_max_age_days).

Usage (from flash-flood-ai/):  python ml/data_processing/export_risk_snapshot.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func  # noqa: E402

from app.config import BASE_DIR  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import District, RiskAssessment  # noqa: E402

OUT_PATH = BASE_DIR / "data" / "processed" / "last_good_risk.json"


def _latest(prob_not_null: bool) -> dict[int, RiskAssessment]:
    """Newest assessment per district, optionally restricted to rows with a
    real probability (same grouped-MAX pattern as main._latest_assessments)."""
    q = db.query(
        RiskAssessment.district_id,
        func.max(RiskAssessment.computed_at).label("mx"),
    )
    if prob_not_null:
        q = q.filter(RiskAssessment.probability.isnot(None))
    sub = q.group_by(RiskAssessment.district_id).subquery()
    rows = (
        db.query(RiskAssessment)
        .join(sub, (RiskAssessment.district_id == sub.c.district_id)
              & (RiskAssessment.computed_at == sub.c.mx))
        .all()
    )
    return {r.district_id: r for r in rows}


def main() -> None:
    districts = db.query(District).all()
    good = _latest(prob_not_null=True)
    latest_all = _latest(prob_not_null=False)

    rows = []
    for d in districts:
        ra = good.get(d.id) or latest_all.get(d.id)
        if ra is None:
            print(f"warning: no assessment at all for {d.name} ({d.shape_id})")
            continue
        rows.append({
            "shape_id": d.shape_id,
            "name": d.name,
            "model_version": ra.model_version,
            "computed_at": ra.computed_at.isoformat(),
            "probability": ra.probability,
            "risk_level": ra.risk_level,
            "trend": ra.trend,
            "history_similarity": ra.history_similarity,
            "confidence": ra.confidence,
            "drivers": ra.drivers,
            "input_freshness": ra.input_freshness,
            "missing_critical": bool(ra.missing_critical),
            "data_cut": ra.data_cut.isoformat() if ra.data_cut else None,
        })

    generated_at = max((datetime.fromisoformat(r["computed_at"]) for r in rows),
                       default=datetime.utcnow())
    payload = {
        "generated_at": generated_at.isoformat(),
        "source": ("latest usable RiskAssessment per district from a full-data "
                   "local run; seeded at bootstrap on ephemeral free-tier hosts"),
        "rows": rows,
    }
    OUT_PATH.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    usable = sum(1 for r in rows if r["probability"] is not None)
    print(f"wrote {OUT_PATH}")
    print(f"rows: {len(rows)} ({usable} with a real probability, "
          f"{len(rows) - usable} honest no-data)")
    print(f"newest computed_at: {generated_at.isoformat()} "
          f"({(datetime.utcnow() - generated_at).total_seconds() / 3600:.1f}h old)")
    print(f"size: {OUT_PATH.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        main()
    finally:
        db.close()
