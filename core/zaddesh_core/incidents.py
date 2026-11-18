from __future__ import annotations

import re
import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from zaddesh_core.config import settings
from zaddesh_core.models.alerts import Alert, AlertRule, Event, MaintenanceWindow
from zaddesh_core.models.checks import Check
from zaddesh_core.models.infrastructure import Device
from zaddesh_core.models.incidents import Incident, IncidentDelivery, IncidentParent, IncidentTimeline
from zaddesh_core.models.metrics import MetricRaw

# Agent-defined alarm states, not invented utilization or temperature thresholds.
# CISCO-ENVMON-MIB and UCD-SNMP-MIB definitions are linked in the audit report.
SNMP_ALARM_STATES = {
    '1.3.6.1.4.1.9.9.13.1.2.1.7.': ('Voltage Critical', {3, 4}, {1}),
    '1.3.6.1.4.1.9.9.13.1.3.1.6.': ('Temperature Critical', {3, 4}, {1}),
    '1.3.6.1.4.1.9.9.13.1.4.1.3.': ('Fan Failure', {3, 4}, {1}),
    '1.3.6.1.4.1.9.9.13.1.5.1.3.': ('Power Supply Failure', {3, 4}, {1}),
    '1.3.6.1.4.1.2021.9.1.100.': ('Disk Space Critical', {1}, {0}),
    '1.3.6.1.4.1.2021.10.1.100.': ('High CPU Load', {1}, {0}),
    '1.3.6.1.4.1.2021.2.1.100.': ('Process Count Critical', {1}, {0}),
    '1.3.6.1.4.1.2021.4.100.': ('Swap Space Critical', {1}, {0}),
}


def evaluate_snmp_rules(db: Session, records: list[dict], checks: dict[int, Check], devices: dict[int, Device], now: datetime) -> None:
    """Evaluate existing scalar rules and documented agent alarm states in the ingest transaction."""
    samples: dict[tuple[int, str], dict[datetime, dict]] = {}
    for row in records:
        check, device = checks.get(row['check_id']), devices.get(row['device_id'])
        if not (check and device and check.is_enabled and check.check_type == 'snmp' and device.is_monitored
                and device.is_priority and row['metric_key'].startswith('snmp.') and row['metric_key'] not in {'snmp.available', 'snmp.supported'}
                and now - timedelta(minutes=5) <= row['timestamp'] <= now and row['tags'].get('supported') != 'false'):
            continue
        times = samples.setdefault((device.id, row['metric_key']), {})
        previous = times.get(row['timestamp'])
        times[row['timestamp']] = ({**row, 'tags': {'supported': 'false'}} if previous and previous['value'] != row['value'] else row)
    if not samples:
        return
    keys = {key for _, key in samples}
    configured: dict[str, list[AlertRule]] = {}
    for rule in db.query(AlertRule).filter(AlertRule.metric_key.in_(keys)).order_by(AlertRule.id):
        if rule.metric_key is not None:
            configured.setdefault(rule.metric_key, []).append(rule)
    comparisons = {'gt': lambda a, b: a > b, 'gte': lambda a, b: a >= b, 'lt': lambda a, b: a < b,
                   'lte': lambda a, b: a <= b, 'eq': lambda a, b: a == b, 'neq': lambda a, b: a != b}
    for (device_id, metric), times in samples.items():
        device = devices[device_id]
        rules = configured.get(metric)
        definitions: list[tuple[Any, str, Any]] = []
        seen = set()
        if rules is not None:
            for rule in rules:
                if (rule.is_enabled and rule.condition_type in {'threshold', 'rate'} and not rule.expression_dsl
                        and rule.operator in comparisons and rule.threshold is not None and math.isfinite(rule.threshold)):
                    signature = (rule.condition_type, rule.operator, rule.threshold, rule.duration_seconds, rule.severity)
                    if signature in seen:
                        continue
                    seen.add(signature)
                    definitions.append((rule, rule.name[:100], None))
        else:
            oid = metric.removeprefix('snmp.').lstrip('.')
            for prefix, (title, failures, healthy) in SNMP_ALARM_STATES.items():
                if oid.startswith(prefix) and oid[len(prefix):].isdigit():
                    definitions.append((None, title, (failures, healthy)))
                    break
        for rule, title, states in definitions:
            key = f'snmp:{device_id}:{rule.id if rule else 0}:{hashlib.sha256(metric.encode()).hexdigest()[:24]}'
            incident = db.query(Incident).filter_by(correlation_key=key).first()
            alert = db.get(Alert, incident.alert_id) if incident else None
            for at, row in sorted(times.items()):
                if row['tags'].get('supported') == 'false':
                    continue
                if incident and incident.transitions and at <= datetime.fromisoformat(incident.transitions[-1]):
                    continue
                value = row['value']
                if rule is not None and rule.condition_type == 'rate':
                    cadence = max(1, checks[row['check_id']].interval_seconds)
                    prior = db.query(MetricRaw.timestamp, MetricRaw.value, MetricRaw.tags).filter(
                        MetricRaw.device_id == device_id, MetricRaw.metric_key == metric, MetricRaw.timestamp < at,
                        MetricRaw.timestamp >= at - timedelta(seconds=cadence * 2)).order_by(MetricRaw.timestamp.desc()).first()
                    if (prior is None or value < prior.value or (prior.tags or {}).get('supported') == 'false'):
                        continue  # Missing baseline/counter reset is unknown, never a rate spike.
                    value = (value - prior.value) / (at-prior.timestamp).total_seconds()
                if states:
                    failures, healthy = states
                    if value not in failures | healthy:
                        continue  # notPresent, notFunctioning, warning, unknown are not proof of failure/recovery.
                    failed = value in failures
                else:
                    failed = comparisons[rule.operator](value, rule.threshold)
                    if failed and rule.duration_seconds > 0:
                        start = at - timedelta(seconds=rule.duration_seconds)
                        history = db.query(MetricRaw.timestamp, MetricRaw.value, MetricRaw.tags).filter(MetricRaw.device_id == device_id,
                            MetricRaw.metric_key == metric, MetricRaw.timestamp >= start - timedelta(seconds=max(1, checks[row['check_id']].interval_seconds)), MetricRaw.timestamp <= at).order_by(
                            MetricRaw.timestamp).limit(2001).all()
                        cadence = max(1, checks[row['check_id']].interval_seconds)
                        if (not history or len(history) > 2000 or history[0].timestamp > start + timedelta(seconds=cadence)
                                or (at - history[0].timestamp).total_seconds() < rule.duration_seconds
                                or (rule.condition_type == 'threshold' and any(not comparisons[rule.operator](item.value, rule.threshold) for item in history))
                                or any((item.tags or {}).get('supported') == 'false' for item in history)
                                or any((right.timestamp-left.timestamp).total_seconds() > cadence * 2 for left, right in zip(history, history[1:]))):
                            continue
                        if rule.condition_type == 'rate':
                            if len(history) < 2 or any(right.value < left.value or right.timestamp <= left.timestamp or
                                not comparisons[rule.operator]((right.value-left.value)/(right.timestamp-left.timestamp).total_seconds(), rule.threshold)
                                for left, right in zip(history, history[1:])):
                                continue
                if alert is None and not failed:
                    continue
                message = redact(f'{device.name} ({device.ip}); {checks[row["check_id"]].name}; {metric} = {value:g}'
                                 + (f'; {rule.operator} {rule.threshold:g} for {rule.duration_seconds}s.' if rule else '; agent alarm state.'))
                if alert is None:
                    alert = Alert(device_id=device_id, check_id=row['check_id'], rule_id=rule.id if rule else None,
                                  title=title, message=message, value=value, severity=rule.severity if rule else 'critical',
                                  status='active', triggered_at=at)
                    db.add(alert)
                    db.flush()
                    incident = Incident(alert_id=alert.id, correlation_key=key, occurrence_count=0, flapping=False, transitions=[])
                    db.add(incident)
                    timeline(db, alert.id, at, 'started', message)
                    opened = True
                else:
                    previously_failed = (alert.value in states[0]) if states else comparisons[rule.operator](alert.value, rule.threshold)
                    opened = alert.status == 'resolved' and failed and not previously_failed
                assert incident is not None
                incident.transitions = [*incident.transitions[-63:], at.isoformat()]
                if alert.status == 'resolved' and failed and not opened:
                    continue  # Keep a manual resolution until a real recovery and new failure.
                alert.value, alert.message = value, message
                if not failed:
                    if alert.status != 'resolved':
                        if not incident.suppression and not (incident.silenced_until and incident.silenced_until > at):
                            queue_delivery(db, alert, 'resolved', at)
                        alert.status, alert.resolved_at = 'resolved', at
                        incident.suppression = None
                        timeline(db, alert.id, at, 'resolved', 'SNMP condition recovered')
                        db.add(Event(device_id=device_id, check_id=alert.check_id, event_type='alert_resolve', severity='info',
                                     title=title, message=message, timestamp=at))
                    continue
                if opened and alert.status == 'resolved':
                    alert.status, alert.triggered_at, alert.resolved_at = 'active', at, None
                    incident.acknowledged_at = None
                    timeline(db, alert.id, at, 'reopened', message)
                reason = suppression_reason(db, device, at)
                if (opened or incident.suppression) and not reason and not (incident.silenced_until and incident.silenced_until > at):
                    queue_delivery(db, alert, 'opened', at)
                incident.suppression = reason
                incident.occurrence_count += 1
                if opened:
                    db.add(Event(device_id=device_id, check_id=alert.check_id, event_type='alert_trigger', severity=alert.severity,
                                 title=title, message=message, timestamp=at))


def evaluate_snmp_observation(db: Session, check: Check, device: Device, values: dict[str, str], now: datetime) -> None:
    """Use the existing snapshots for alarm states and explicitly configured scalar rules."""
    if not device.is_priority:
        return
    configured = {key for key, in db.query(AlertRule.metric_key).filter(AlertRule.metric_key.is_not(None))}
    records = []
    for oid, raw in values.items():
        metric = 'snmp.' + oid.lstrip('.')
        if metric not in configured and not any(oid.lstrip('.').startswith(prefix) for prefix in SNMP_ALARM_STATES):
            continue
        try:
            value = float(raw)
        except (ValueError, TypeError):
            continue
        if not math.isfinite(value):
            continue
        row = dict(device_id=device.id, check_id=check.id, metric_key=metric, value=value, timestamp=now, tags={'source': 'snmp_inventory'})
        records.append(row)
        db.add(MetricRaw(**row))
    if records:
        db.flush()
        evaluate_snmp_rules(db, records, {check.id: check}, {device.id: device}, now)


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
    device = db.get(Device, alert.device_id)
    if device is None or not device.is_priority:
        return
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
    incidents: dict[str, tuple[Incident, Alert]],
    maintenance: dict[tuple[str, int | None], list[MaintenanceWindow]],
    down_parents: set[int],
) -> None:
    key = f"availability:{device.id}:{check.id}"
    existing = incidents.get(key)
    incident, alert = existing if existing is not None else (None, None)
    changed = (previous != ("ok" if ok else "critical"))
    if changed:
        db.add(Event(device_id=device.id, check_id=check.id, event_type="state_change",
                     severity="info" if ok else 'warning' if check.check_type == 'snmp' else "critical",
                     title=('SNMP Recovered' if ok else 'SNMP Unreachable') if check.check_type == 'snmp' else f"Availability: {check.name}",
                     message="Probe passed" if ok else "Probe failed", timestamp=timestamp))
    if not device.is_priority or (check.check_type == "snmp" and device.collection_mode != "on_demand"
            and check.params.get("oid", "1.3.6.1.2.1.1.3.0").lstrip(".") != "1.3.6.1.2.1.1.3.0"):
        return
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
            alert = Alert(device_id=device.id, check_id=check.id,
                          title="SNMP Unreachable" if check.check_type == 'snmp' else f"Availability: {check.name}",
                          message=f"{device.name} ({device.ip}); {check.name}: " + (
                              'SNMP communication failed; verify network access, credentials and agent availability. Device outage is unconfirmed.'
                              if check.check_type == 'snmp' else f'{check.check_type.upper()} probe failed.'),
                          severity="warning" if check.check_type == 'snmp' else "critical", status="active", triggered_at=timestamp)
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
        incidents[key] = (incident, alert)
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
    scopes = [("all", None), ("device", device.id), ("site", device.site_id), ("group", device.group_id)]
    if any(window.start_time <= timestamp < window.end_time
           for kind, scope in scopes if kind == "all" or scope is not None
           for window in maintenance.get((kind, scope), ())):
        reason: str | None = "maintenance"
    else:
        reason = "parent down" if device.id in down_parents else None
    if (changed or incident.suppression) and not reason and not (incident.silenced_until and incident.silenced_until > timestamp):
        queue_delivery(db, alert, "opened", timestamp)
    if reason != incident.suppression:
        timeline(db, alert.id, timestamp, "suppressed" if reason else "unsuppressed", reason or "Suppression ended")
    incident.suppression = reason


def availability_context(db: Session, checks: list[Check], start: datetime, end: datetime) -> tuple[
    dict[str, tuple[Incident, Alert]], dict[tuple[str, int | None], list[MaintenanceWindow]], set[int],
]:
    keys = [f"availability:{check.device_id}:{check.id}" for check in checks]
    incidents = {incident.correlation_key: (incident, alert) for incident, alert in db.execute(
        select(Incident, Alert).join(Alert, Alert.id == Incident.alert_id).where(Incident.correlation_key.in_(keys))
    )}
    maintenance: dict[tuple[str, int | None], list[MaintenanceWindow]] = {}
    for window in db.scalars(select(MaintenanceWindow).where(
        MaintenanceWindow.start_time <= end, MaintenanceWindow.end_time > start,
    )):
        scope = None if window.scope_type == "all" else window.scope_id
        maintenance.setdefault((window.scope_type, scope), []).append(window)
    down_parents = set(db.scalars(select(IncidentParent.device_id).join(
        Device, Device.id == IncidentParent.parent_id,
    ).where(IncidentParent.device_id.in_({check.device_id for check in checks}), Device.status == "down")))
    return incidents, maintenance, down_parents
