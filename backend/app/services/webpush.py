"""Web Push delivery for HIGH-risk alerts (internal browser notifications).

No accounts, no user data: the only stored state is the browser-generated
push endpoint + keys (BrowserNotifyEndpoint). VAPID keys come from env
(VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT); the private key never
leaves the server. Any failure here is logged and swallowed — the flood
prediction and alert pipeline must never crash because of notifications.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..models import BrowserNotifyEndpoint

_PUSH: dict | None = None
_PUBLIC_KEY_CACHE: str | None = None


def _load_push():
    """Lazily import pywebpush so the API works without it installed."""
    global _PUSH
    if _PUSH is None:
        try:
            from pywebpush import webpush, WebPushException  # noqa: F401
            _PUSH = {"send": webpush}
        except ImportError:
            _PUSH = {"send": None}
    return _PUSH


def vapid_configured() -> bool:
    return bool(_public_key() and _private_key())


def _public_key() -> str:
    import os
    return os.getenv("VAPID_PUBLIC_KEY", "") or getattr(_settings(), "vapid_public_key", "")


def _private_key() -> str:
    import os
    return os.getenv("VAPID_PRIVATE_KEY", "") or getattr(_settings(), "vapid_private_key", "")


def _settings():
    from ..config import settings
    return settings


def public_key() -> str | None:
    """Application server key for the browser's push registration."""
    global _PUBLIC_KEY_CACHE
    if _PUBLIC_KEY_CACHE is None:
        _PUBLIC_KEY_CACHE = _public_key() or None
    return _PUBLIC_KEY_CACHE


def register_endpoint(db: Session, endpoint: str, p256dh: str, auth: str,
                      district_id: int | None = None) -> BrowserNotifyEndpoint:
    """Store/refresh a browser's push endpoint (internal; idempotent)."""
    if not endpoint or not p256dh or not auth:
        raise ValueError("missing push registration data")
    if len(endpoint) > 512 or not endpoint.startswith("https://"):
        raise ValueError("invalid push endpoint")
    row = db.query(BrowserNotifyEndpoint).filter(
        BrowserNotifyEndpoint.endpoint == endpoint).first()
    if row:
        row.p256dh, row.auth = p256dh, auth
        row.active = True
        row.last_error = None
        if district_id is not None:
            row.last_district_id = district_id
    else:
        row = BrowserNotifyEndpoint(
            endpoint=endpoint, p256dh=p256dh, auth=auth,
            last_district_id=district_id)
        db.add(row)
    db.commit()
    return row


def deactivate_endpoint(db: Session, endpoint: str) -> None:
    """Browser asked to stop notifications (or endpoint expired)."""
    row = db.query(BrowserNotifyEndpoint).filter(
        BrowserNotifyEndpoint.endpoint == endpoint).first()
    if row:
        row.active = False
        db.commit()


def _payload(alert, district_name: str, state: str) -> str:
    """Build the notification payload from an EXISTING alert record."""
    reason = ""
    try:
        lines = [l.strip("- ").strip() for l in alert.message.splitlines()
                 if l.startswith("- ")]
        if lines:
            reason = lines[0].rstrip(".") + "."
    except Exception:
        reason = ""
    body = (f"{district_name} is currently at {alert.risk_level} flood risk. "
            f"Tap to view details.")
    if reason:
        body = f"{reason} {district_name} is at {alert.risk_level} flood risk. Tap to view details."
    return json.dumps({
        "title": f"🚨 HIGH Flood Risk — {district_name}",
        "body": body[:220],
        "district_id": alert.district_id,
        "district": district_name,
        "state": state,
        "alert_type": alert.alert_type,
        "url": f"/location/{alert.district_id}",
    })


def send_alert_to_all(db: Session, alert) -> int:
    """Push one existing HIGH-risk alert to every active browser endpoint.
    Returns the number of successful deliveries; never raises."""
    if not vapid_configured():
        print("[webpush] skipped: VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY not configured")
        return 0
    send = _load_push()["send"]
    if send is None:
        print("[webpush] skipped: pywebpush not installed")
        return 0

    from ..models import District
    d = db.get(District, alert.district_id)
    payload = _payload(alert, d.name if d else f"district {alert.district_id}",
                       d.state if d else "")

    sent = 0
    endpoints = (db.query(BrowserNotifyEndpoint)
                 .filter(BrowserNotifyEndpoint.active.is_(True)).all())
    for ep in endpoints:
        try:
            send(
                subscription_info={
                    "endpoint": ep.endpoint,
                    "keys": {"p256dh": ep.p256dh, "auth": ep.auth},
                },
                data=payload,
                vapid_private_key=_private_key(),
                vapid_claims={"sub": os_subject()},
            )
            ep.last_success_at = datetime.utcnow()
            ep.last_error = None
            sent += 1
        except Exception as e:
            msg = str(e)
            ep.last_error_at = datetime.utcnow()
            ep.last_error = msg[:200]
            # 404/410 = the browser no longer knows this endpoint: deactivate
            if "410" in msg or "404" in msg or "gone" in msg.lower():
                ep.active = False
            print(f"[webpush] delivery failed for endpoint …{ep.endpoint[-20:]}: {msg[:120]}")
    db.commit()
    return sent


def os_subject() -> str:
    import os
    return (os.getenv("VAPID_SUBJECT", "")
            or getattr(_settings(), "vapid_subject", "")) \
        or "mailto:admin@flashflood.local"


def count_active(db: Session) -> int:
    return (db.query(BrowserNotifyEndpoint)
            .filter(BrowserNotifyEndpoint.active.is_(True)).count())


def generate_vapid_keys() -> dict:
    """One-time helper: prints fresh VAPID keys for the .env (server-side only)."""
    from py_vapid import Vapid
    import base64
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
    )
    v = Vapid()
    v.generate_keys()
    private = v.private_key
    priv = private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
    pub = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return {
        "VAPID_PRIVATE_KEY": base64.urlsafe_b64encode(priv).decode().rstrip("="),
        "VAPID_PUBLIC_KEY": base64.urlsafe_b64encode(pub).decode().rstrip("="),
        "VAPID_SUBJECT": "mailto:you@example.com",
        "_note": "put these in flash-flood-ai/.env — never commit them",
    }
