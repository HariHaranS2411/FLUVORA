"""Last-known-good fallback for ephemeral free-tier deployments.

Render's free plan gives every spin-up an empty database, and Open-Meteo's
shared-egress-IP quota is often exhausted exactly there — so a fresh boot used
to serve 735 "no data" districts until a quota window opened, even though the
model had produced perfectly good (if older) estimates before.

This module seeds the most recent usable assessment per district, exported from
a full-data run into data/processed/last_good_risk.json (shipped in git), into
any database that lacks them. The rows keep their ORIGINAL computed_at and
data_cut, so every surface that displays data age stays truthful: the UI
literally shows "last computed <date>" rather than pretending the numbers are
live. When live fetches succeed again, newer real rows simply outrank these
(see main._latest_assessments, which prefers the newest assessment with a real
probability), and the stale cohort is eventually pruned as usual.

Seeding is idempotent on the (district_id, computed_at) unique key and is
called from two places:
  - the startup bootstrap, so an empty DB serves data immediately; and
  - after each refresh cycle's prune, because the snapshot cohort carries the
    oldest computed_at and would otherwise fall out of the 30-run retention
    window during a long quota outage.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import BASE_DIR, settings
from ..models import District, RiskAssessment

SNAPSHOT_PATH = BASE_DIR / "data" / "processed" / "last_good_risk.json"


def _read_snapshot() -> list[dict] | None:
    """Parse and age-check the snapshot. Returns None when missing, corrupt,
    or older than risk_snapshot_max_age_days (stale estimates must not masquerade
    as an early-warning baseline forever)."""
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        rows = payload["rows"]
        generated_at = datetime.fromisoformat(payload["generated_at"])
        if not isinstance(rows, list):
            return None
    except Exception:  # noqa: BLE001 — any defect in the artifact means "no fallback"
        return None
    if datetime.utcnow() - generated_at > timedelta(days=settings.risk_snapshot_max_age_days):
        print(f"[snapshot] last_good_risk.json is older than "
              f"{settings.risk_snapshot_max_age_days} days — not seeding stale estimates")
        return None
    return rows


def seed_missing_from_snapshot(db: Session) -> int:
    """Insert snapshot assessments whose (district_id, computed_at) pair is not
    already stored. Idempotent, never overwrites live rows, never fabricates:
    every value originates from a real model run recorded in the snapshot.
    Districts are matched by shape_id, so district primary keys may differ
    between the exporting and the receiving database. Returns rows added."""
    rows = _read_snapshot()
    if not rows:
        return 0

    id_by_shape = {d.shape_id: d.id for d in db.query(District).all()}
    existing = set(
        db.query(RiskAssessment.district_id, RiskAssessment.computed_at).all()
    )
    added = 0
    for r in rows:
        did = id_by_shape.get(r.get("shape_id"))
        if did is None:
            continue
        computed_at = datetime.fromisoformat(r["computed_at"])
        if (did, computed_at) in existing:
            continue
        data_cut = r.get("data_cut")
        db.add(RiskAssessment(
            district_id=did,
            model_version=r["model_version"],
            computed_at=computed_at,
            probability=r.get("probability"),
            risk_level=r.get("risk_level"),
            trend=r.get("trend") or "stable",
            history_similarity=r.get("history_similarity"),
            confidence=r.get("confidence"),
            drivers=r.get("drivers"),
            input_freshness=r.get("input_freshness"),
            missing_critical=bool(r.get("missing_critical")),
            data_cut=datetime.fromisoformat(data_cut) if data_cut else None,
        ))
        existing.add((did, computed_at))
        added += 1
    if added:
        db.commit()
        age_h = (datetime.utcnow() - max(
            datetime.fromisoformat(r["computed_at"]) for r in rows
        )).total_seconds() / 3600
        print(f"[snapshot] seeded {added} last-good assessments "
              f"(data age ~{age_h:.0f}h — shown honestly in the UI)")
    return added
