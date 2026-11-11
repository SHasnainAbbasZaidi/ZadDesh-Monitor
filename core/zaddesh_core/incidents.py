from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from zaddesh_core.config import settings
from zaddesh_core.models.alerts import Alert, Event, MaintenanceWindow
from zaddesh_core.models.checks import Check
from zaddesh_core.models.infrastructure import Device
from zaddesh_core.models.incidents import Incident, IncidentDelivery, IncidentParent, IncidentTimeline


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


SECRET_KEY = re.compile(r"password|passphrase|secret|community|token|authorization|private[_ -]?key|(?:auth|priv)[_ -]?key", re.I)
SECRET_VALUE = re.compile(
    r"""(?ix)
    (\b(?:[\w-]*(?:password|passphrase|secret|community|token|authorization)[\w-]*|(?:auth|priv|private)[_ -]?key)
    ["']?\s*[:=]\s*)(?:["'][^"'\r\n]*["']|(?:bearer|basic)\s+[^\s,;]+|[^\s,;}&]+)
    """
)
BEARER = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+|\bzds_[A-Za-z0-9_-]+", re.I)
URI_CREDENTIALS = re.compile(r"(://)[^\s/@]+:[^\s/@]+@")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[redacted]" if SECRET_KEY.search(key) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return URI_CREDENTIALS.sub(r"\1[redacted]@", BEARER.sub("[redacted]", SECRET_VALUE.sub(r"\1[redacted]", value)))
    return value


def timeline(db: Session, alert_id: int, timestamp: datetime, kind: str, message: str, actor: str | None = None) -> None:
    db.add(IncidentTimeline(alert_id=alert_id, timestamp=timestamp, kind=kind, message=message, actor=actor))


def queue_delivery(db: Session, alert: Alert, event: str, timestamp: datetime) -> None:
    if settings.features.incident_webhook_url and not settings.demo_mode and alert.severity != "info":
        db.add(IncidentDelivery(alert_id=alert.id, event=event, next_attempt=utc_now(), payload=redact({
            "alert_id": alert.id, "event": event, "title": alert.title, "message": alert.message,
            "severity": alert.severity, "timestamp": timestamp.replace(tzinfo=timezone.utc).isoformat(),
        })))


def suppression_reason(db: Session, device: Device, at: datetime) -> str | None:
    scopes = [MaintenanceWindow.scope_type == "all",
              and_(MaintenanceWindow.scope_type == "device", MaintenanceWindow.scope_id == device.id)]
    for kind, value in (("site", device.site_id), ("group", device.group_id)):
        if value is not None:
            scopes.append(and_(MaintenanceWindow.scope_type == kind, MaintenanceWindow.scope_id == value))
    if db.query(MaintenanceWindow.id).filter(
        MaintenanceWindow.start_time <= at, MaintenanceWindow.end_time > at, or_(*scopes),
    ).first():
        return "maintenance"
    parent = db.get(IncidentParent, device.id)
    if parent and db.query(Device.id).filter(Device.id == parent.parent_id, Device.status == "down").first():
        return "parent down"
    return None


def record_availability(
    db: Session, check: Check, device: Device, previous: str, ok: bool, timestamp: datetime,
) -> None:
    key = f"availability:{device.id}:{check.id}"
    incident = db.query(Incident).filter_by(correlation_key=key).first()
    alert = db.get(Alert, incident.alert_id) if incident else None
    changed = (previous != ("ok" if ok else "critical"))
    if changed:
        db.add(Event(device_id=device.id, check_id=check.id, event_type="state_change",
                     severity="info" if ok else "critical", title=f"Availability: {check.name}",
                     message="Probe passed" if ok else "Probe failed", timestamp=timestamp))
    if alert is None:
        if ok and previous != "critical":
            return
        # Adopt an open legacy probe alert during opt-in.
        alert = db.query(Alert).filter(
            Alert.check_id == check.id, Alert.rule_id.is_(None),
            Alert.title == f"Availability: {check.name}", Alert.status.in_(("active", "acknowledged")),
        ).order_by(Alert.id.desc()).first()
        if alert is None and ok:
            return
        if alert is None:
            alert = Alert(device_id=device.id, check_id=check.id, title=f"Availability: {check.name}",
                          message=f"{check.check_type.upper()} probe for {device.name} failed on {check.name}.",
                          severity="critical", status="active", triggered_at=timestamp)
            db.add(alert)
            db.flush()
        incident = db.get(Incident, alert.id)
        if incident is None:
            incident = Incident(alert_id=alert.id, correlation_key=key, occurrence_count=0,
                                flapping=False, transitions=[])
            db.add(incident)
            timeline(db, alert.id, alert.triggered_at, "started", alert.message or "Probe failed")
        else:
            incident.correlation_key = key
    assert incident is not None
    if changed:
        cutoff = (timestamp - timedelta(minutes=10)).isoformat()
        incident.transitions = [value for value in incident.transitions if value >= cutoff][-63:] + [timestamp.isoformat()]
        if len(incident.transitions) >= 5 and not incident.flapping:
            incident.flapping = True
            timeline(db, alert.id, timestamp, "flapping", "Five or more state transitions in ten minutes")
    if ok:
        if alert.status != "resolved":
            if not incident.suppression and not (incident.silenced_until and incident.silenced_until > timestamp):
                queue_delivery(db, alert, "resolved", timestamp)
            alert.status = "resolved"
            alert.resolved_at = timestamp
            incident.suppression = None
            timeline(db, alert.id, timestamp, "resolved", "Probe recovered")
        return
    if alert.status == "resolved" and not changed:
        return  # A manual resolution stays in effect until a new down transition.
    if alert.status == "resolved":
        alert.status = "active"
        alert.triggered_at = timestamp
        alert.resolved_at = None
        incident.acknowledged_at = None
        timeline(db, alert.id, timestamp, "reopened", "Probe failed again")
    incident.occurrence_count += 1
    if incident.occurrence_count > 1:
        timeline(db, alert.id, timestamp, "occurrence", "Probe failed")
    reason = suppression_reason(db, device, timestamp)
    if (changed or incident.suppression) and not reason and not (incident.silenced_until and incident.silenced_until > timestamp):
        queue_delivery(db, alert, "opened", timestamp)
    if reason != incident.suppression:
        timeline(db, alert.id, timestamp, "suppressed" if reason else "unsuppressed", reason or "Suppression ended")
    incident.suppression = reason
