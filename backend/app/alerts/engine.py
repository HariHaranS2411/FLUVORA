"""Alert engine (spec §14, §15, §30).

- Alert types: WATCH / ADVISORY / HIGH_RISK / IMMINENT / DATA_UNAVAILABLE,
  mapped deterministically from the model-card probability thresholds
  (documented criteria — never arbitrary).
- Deduplication via event_key = "{district}-{alert_type}-{risk_level}" with a
  per-district cooldown; unchanged conditions do not re-notify.
- Every message uses probabilistic language and carries the safety disclaimer.
- Channels: web notification payload now; email if SMTP_* configured;
  SMS/push interfaces stubbed with explicit 'not configured' status (no fakes).
"""
from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from sqlalchemy.orm import Session

from ..models import Alert, District, RiskAssessment, Subscription

COOLDOWN_HOURS = 12

# Hysteresis factor: entering the HIGH band requires 25% more probability headroom
# (or an increasing trend) than simply being above the threshold.
ENTRY_FACTOR = 1.25


def _load_thresholds() -> dict:
    """Real thresholds from the trained model card (no hardcoding)."""
    from ..config import BASE_DIR
    p = BASE_DIR / "data" / "processed" / "model_card.json"
    if p.exists():
        return json.loads(p.read_text()).get("thresholds", {})
    return {}


THRESHOLDS = _load_thresholds()

SAFETY = (
    "This prediction is generated from available data and statistical/ML models. "
    "It is not a guarantee that flooding will or will not occur. Follow official "
    "warnings and instructions from relevant disaster-management authorities."
)

# Documented mapping risk-level -> alert type (thresholds come from the model card)
ALERT_FOR_LEVEL = {
    "LOW": None,               # no alert for low risk
    "MODERATE": "WATCH",
    "HIGH": "HIGH_RISK",
    "CRITICAL": "IMMINENT",
    "DATA_UNAVAILABLE": "DATA_UNAVAILABLE",
}


def generate_alerts(db: Session, run_id: datetime | None = None) -> list[Alert]:
    run_id = run_id or datetime.utcnow()
    created: list[Alert] = []
    brand_new_high: list[Alert] = []  # first-time HIGH/CRITICAL alerts this run
    # Latest assessment per district (robust to refresh/alert running separately)
    from sqlalchemy import func

    subq = (
        db.query(RiskAssessment.district_id,
                 func.max(RiskAssessment.computed_at).label("mx"))
        .group_by(RiskAssessment.district_id).subquery()
    )
    latest = (
        db.query(RiskAssessment)
        .join(subq, (RiskAssessment.district_id == subq.c.district_id)
              & (RiskAssessment.computed_at == subq.c.mx))
        .all()
    )
    for ra in latest:
        try:
            d = db.query(District).get(ra.district_id)
            alert_type = ALERT_FOR_LEVEL.get(ra.risk_level or "")
            if alert_type is None:
                # Risk resolved to LOW: clear any active HIGH/CRITICAL alert so a
                # later genuine re-escalation (LOW -> HIGH) raises a NEW alert
                # and a new browser notification instead of hitting the cooldown.
                resolved = (
                    db.query(Alert)
                    .filter(Alert.district_id == ra.district_id,
                            Alert.risk_level.in_(["HIGH", "CRITICAL"]))
                    .delete(synchronize_session=False)
                )
                if resolved:
                    db.commit()
                continue
            if ra.risk_level == "MODERATE":
                # Also resolve on a drop to MODERATE (spec: MODERATE -> HIGH is a
                # new escalation). Re-entry into HIGH still needs the hysteresis
                # evidence bar (1.25x threshold or increasing trend), so borderline
                # threshold flapping cannot spam notifications.
                resolved = (
                    db.query(Alert)
                    .filter(Alert.district_id == ra.district_id,
                            Alert.risk_level.in_(["HIGH", "CRITICAL"]))
                    .delete(synchronize_session=False)
                )
                if resolved:
                    db.commit()
            if ra.risk_level == "DATA_UNAVAILABLE" and not ra.missing_critical:
                continue

            # --- hysteresis: require STRONGER evidence to enter HIGH/CRITICAL
            # than to remain there. Entry needs prob >= threshold * ENTRY_FACTOR
            # (1.25) OR an increasing trend; remaining only needs the plain
            # threshold. This stops LOW<->HIGH flapping on small changes.
            fresh = json.loads(ra.input_freshness) if ra.input_freshness else {}
            data_ok = not ra.missing_critical and all(
                v in ("fresh", "stale") for v in fresh.values())
            if not data_ok:
                # Never alert on missing/insufficient data — silence is honest.
                continue

            high_or_above = ra.risk_level in ("HIGH", "CRITICAL")
            if high_or_above:
                prev_high = (
                    db.query(Alert)
                    .filter(Alert.district_id == d.id,
                            Alert.risk_level.in_(["HIGH", "CRITICAL"]))
                    .order_by(Alert.created_at.desc())
                    .first()
                )
                was_high = bool(prev_high and prev_high.created_at >
                                datetime.utcnow() - timedelta(hours=72))
                thr_high = THRESHOLDS.get("high")
                if (not was_high and thr_high is not None
                        and ra.probability is not None
                        and ra.probability < thr_high * ENTRY_FACTOR
                        and ra.trend != "increasing"):
                    continue  # not enough evidence to ENTER the high band yet

            event_key = f"{d.id}-{alert_type}-{ra.risk_level}"
            existing = (
                db.query(Alert)
                .filter(Alert.district_id == d.id, Alert.event_key == event_key)
                .order_by(Alert.created_at.desc())
                .first()
            )
            if existing and existing.created_at > datetime.utcnow() - timedelta(hours=COOLDOWN_HOURS):
                continue  # dedup: same unchanged condition inside cooldown
            prob_txt = f"{ra.probability * 100:.0f}%" if ra.probability is not None else "N/A"
            drivers = json.loads(ra.drivers) if ra.drivers else []
            reasons = "\n".join(f"- {x}" for x in drivers[:4]) or "- Model inputs within historical norms"
            message = (
                f"HIGH-FLOW / HYDROLOGICAL RISK ALERT\n\n"
                f"Area: {d.name}, {d.state} (district-level assessment)\n"
                f"Risk level: {ra.risk_level} ({ra.trend})\n"
                f"Prediction horizon: 0-24 h (nowcast of today's high-flow state)\n"
                f"Model-estimated probability of high-flow conditions: {prob_txt}\n"
                f"(hydrological threshold exceedance estimate - not an official "
                f"flood warning and not a confirmed-flood probability)\n"
                f"Reason (model sensitivities):\n{reasons}\n"
                f"Measurements: rainfall/soil/discharge as of data cut "
                f"{ra.data_cut.isoformat() if ra.data_cut else 'N/A'}; "
                f"freshness: {', '.join(f'{k}={v}' for k, v in fresh.items()) or 'unknown'}\n"
                f"Sources: ERA5 (Open-Meteo), GloFAS (Open-Meteo Flood API), GDACS\n"
                f"Computed: {ra.computed_at.isoformat()} with model {ra.model_version}\n\n"
                f"{SAFETY}"
            )
            if existing:
                # Cooldown expired: refresh the existing row in place — the UNIQUE
                # (district_id, event_key) constraint allows only one row per key.
                existing.alert_type = alert_type
                existing.risk_level = ra.risk_level
                existing.probability = ra.probability
                existing.message = message
                existing.created_at = run_id
                existing.expires_at = run_id + timedelta(hours=24)
                db.flush()
                created.append(existing)
            else:
                a = Alert(
                    district_id=d.id, alert_type=alert_type, risk_level=ra.risk_level,
                    probability=ra.probability, message=message,
                    event_key=event_key, created_at=run_id,
                    expires_at=run_id + timedelta(hours=24),
                )
                db.add(a)
                db.flush()  # surface IntegrityError here, not at the final commit
                created.append(a)
                if high_or_above:
                    brand_new_high.append(a)
        except Exception as e:  # noqa: BLE001
            # One bad district must not abort alerting for every other district.
            db.rollback()
            print(f"alert generation skipped for district {ra.district_id}: "
                  f"{type(e).__name__} {str(e).encode('ascii', 'replace').decode()[:200]}")
    db.commit()
    _notify(db, created)
    # Browser notifications for genuinely NEW HIGH-risk alerts only.
    # Refreshed rows (cooldown expiry) and duplicates never reach this list.
    # A notification failure must never break the alert pipeline.
    if brand_new_high:
        try:
            from ..services.webpush import send_alert_to_all
            n = 0
            for a in brand_new_high:
                n += send_alert_to_all(db, a)
            if n:
                print(f"[webpush] {n} browser notification(s) sent for "
                      f"{len(brand_new_high)} new HIGH-risk alert(s)")
        except Exception as e:  # noqa: BLE001
            print(f"[webpush] notification pass failed (alerting unaffected): {e}")
    return created


def _notify(db: Session, alerts: list[Alert]) -> None:
    if not alerts:
        return
    by_district = {a.district_id: a for a in alerts}
    subs = (
        db.query(Subscription)
        .filter(Subscription.active.is_(True),
                Subscription.district_id.in_(by_district.keys()))
        .all()
    )
    for s in subs:
        a = by_district.get(s.district_id)
        if not a or not _meets_min(a.alert_type, s.min_alert_type):
            continue
        if s.email:
            _send_email(s.email, a)


def _meets_min(alert_type: str, min_type: str) -> bool:
    order = ["DATA_UNAVAILABLE", "WATCH", "ADVISORY", "HIGH_RISK", "IMMINENT"]
    return order.index(alert_type) >= order.index(min_type)


def _send_email(to: str, alert: Alert) -> bool:
    host = os.getenv("SMTP_HOST", "")
    if not host:
        print(f"[email not configured] would notify {to}: {alert.alert_type} "
              f"district={alert.district_id}")
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[Flash Flood {alert.alert_type}] risk update"
        msg["From"] = os.getenv("SMTP_FROM", "alerts@example.com")
        msg["To"] = to
        msg.set_content(alert.message)
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as smtp:
            smtp.starttls()
            smtp.login(os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"email send failed: {e}")
        return False
