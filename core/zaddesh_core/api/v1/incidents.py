from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field
from pymongo.errors import PyMongoError
from sqlalchemy import and_, case, func, or_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import get_current_user, require_role
from zaddesh_core.api.v1.logs import log_collection
from zaddesh_core.api.v1.stream import notify_change
from zaddesh_core.api.v1.zabbix import HOST_FIELDS, hidden_host_ids, local_matches, saved_api, source_key, visible
from zaddesh_core.config import settings
from zaddesh_core.database import get_db
from zaddesh_core.incidents import queue_delivery, redact, timeline, utc_now
from zaddesh_core.models.alerts import Acknowledgement, Alert, Event, MaintenanceWindow
from zaddesh_core.models.auth import AuditLog, User
from zaddesh_core.models.checks import Check
from zaddesh_core.models.incidents import Incident, IncidentDelivery, IncidentFilter, IncidentParent, IncidentTimeline
from zaddesh_core.models.infrastructure import Collector, Device, Site
from zaddesh_core.models.metrics import MetricRaw
from zaddesh_core.models.zabbix import ZabbixAlertDismissal, ZabbixConnection


def enabled() -> None:
    if not settings.features.incident_workspace:
        raise HTTPException(404, "Incident workspace is disabled")


router = APIRouter(prefix="/incidents", dependencies=[Depends(enabled)], tags=["Incident workspace"])
ACTIVE_ALERT_WINDOW = timedelta(hours=8)
CONNECTION_EVENT_TYPES = ("connect", "disconnect", "connected", "disconnected", "reconnect", "reconnected",
                          "connection_opened", "connection_closed", "connection_lost", "connection_restored")
ZABBIX_SEVERITY = {"0": "info", "1": "info", "2": "warning", "3": "average", "4": "high", "5": "critical"}
ZABBIX_OFFLINE = re.compile(r"\b(?:device|host) (?:is )?(?:offline|down)\b|\bunavailable by icmp\b|\bno icmp ping\b", re.I)
ZABBIX_LINK = re.compile(r"\b(?:uplink|downlink)\b.*\b(?:down|offline|unavailable)\b|\blink down\b.*\b(?:uplink|downlink)\b", re.I)
ZABBIX_NOISE = re.compile(r"^(?:dis)?connect(?:ed|ion)?(?:\b|:)|\b(?:dis)?connected\b|^reconnected\b", re.I)


def connection_noise(title):
    return or_(func.lower(func.trim(title)).in_(CONNECTION_EVENT_TYPES),
               title.ilike("connect %"), title.ilike("disconnect %"), title.ilike("connect:%"),
               title.ilike("disconnect:%"), title.ilike("connection %"), title.ilike("connected%"),
               title.ilike("disconnected%"), title.ilike("reconnected%"),
               title.icontains(" disconnect", autoescape=True), title.icontains(" connected", autoescape=True))


def alert_category():
    link = or_(func.coalesce(Incident.correlation_key, "").startswith("link:"),
               Alert.title.ilike("link down:%"), Alert.title.ilike("uplink down%"), Alert.title.ilike("downlink down%"))
    offline = or_(Alert.title.ilike("device offline%"), Alert.title.ilike("device down%"),
                  and_(Alert.rule_id.is_(None), Alert.title.startswith("Availability: "),
                       Check.check_type == "icmp", Device.status == "down"))
    return case((link, "link_down"), (offline, "device_offline"), else_="other")


def alert_not_dismissed():
    return or_(Alert.dismissed_episode_at.is_(None), Alert.dismissed_episode_at != Alert.triggered_at)


def naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def alert_summary(alert, incident, device, check, site, category):
    now = utc_now()
    reason = incident.suppression if incident else None
    if incident and incident.silenced_until and incident.silenced_until > now and alert.status != "resolved":
        reason = "silenced"
    return {
        "id": alert.id, "title": redact(alert.title), "severity": alert.severity, "status": alert.status, "category": category,
        "suppression": reason if alert.status != "resolved" else None, "started_at": iso(alert.triggered_at),
        "resolved_at": iso(alert.resolved_at), "acknowledged_at": iso(incident.acknowledged_at if incident else None),
        "duration_seconds": max(0, int(((alert.resolved_at or now) - alert.triggered_at).total_seconds())),
        "occurrences": incident.occurrence_count if incident else 1, "flapping": bool(incident and incident.flapping),
        "type": "link" if incident and incident.correlation_key.startswith("link:") else
        "availability" if alert.rule_id is None and alert.title.startswith("Availability: ") else "rule",
        "device_id": device.id, "device": device.name, "check": check.name if check else None,
        "site": site.name if site else None,
    }


def joined_alerts(db: Session):
    return (db.query(Alert, Incident, Device, Check, Site, alert_category()).join(Device, Alert.device_id == Device.id)
            .outerjoin(Incident, Incident.alert_id == Alert.id).outerjoin(Check, Check.id == Alert.check_id)
            .outerjoin(Site, Site.id == Device.site_id))


@router.get("/alerts")
def list_incidents(
    q: str = Query("", max_length=120), severity: str | None = None, status: str | None = None,
    device_id: int | None = None, site_id: int | None = None,
    type: Literal["availability", "link", "rule"] | None = None,
    category: Literal["critical", "device_offline", "link_down", "other"] | None = None,
    view: Literal["all", "current", "history"] = "all", hide_connection_noise: bool = False,
    include_count: bool = False,
    start: AwareDatetime | None = None, end: AwareDatetime | None = None,
    sort: Literal["newest", "oldest"] = "newest", offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    _: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    query = joined_alerts(db)
    if view != "all":
        cutoff = utc_now() - ACTIVE_ALERT_WINDOW
        query = query.filter(alert_not_dismissed())
        if view == "current":
            query = query.filter(Alert.status != "resolved", Alert.triggered_at > cutoff)
        else:
            query = query.filter(or_(Alert.status == "resolved", Alert.triggered_at <= cutoff))
    classification = alert_category()
    if category:
        query = query.filter(classification.in_(("device_offline", "link_down")) if category == "critical"
                             else classification == category)
    if hide_connection_noise:
        query = query.filter(or_(classification != "other", ~connection_noise(Alert.title)))
    if q:
        query = query.filter(or_(Alert.title.icontains(q, autoescape=True), Device.name.icontains(q, autoescape=True),
                                 Check.name.icontains(q, autoescape=True)))
    if status == "suppressed":
        query = query.filter(Alert.status != "resolved", or_(Incident.suppression.is_not(None),
                                                             Incident.silenced_until > utc_now()))
    elif status:
        query = query.filter(Alert.status == status)
    availability = Alert.rule_id.is_(None) & Alert.title.startswith("Availability: ")
    link_type = func.coalesce(Incident.correlation_key, "").startswith("link:")
    if type:
        query = query.filter({"availability": availability, "link": link_type, "rule": ~availability & ~link_type}[type])
    for column, value in ((Alert.severity, severity), (Alert.device_id, device_id), (Device.site_id, site_id)):
        if value is not None:
            query = query.filter(column == value)
    if start:
        query = query.filter(Alert.triggered_at >= naive_utc(start))
    if end:
        query = query.filter(Alert.triggered_at <= naive_utc(end))
    total = query.count() if include_count else None
    order = Alert.triggered_at.asc() if sort == "oldest" else Alert.triggered_at.desc()
    rows = query.order_by(case((Alert.status == "resolved", 1), else_=0), order, Alert.id).offset(offset).limit(limit + 1).all()
    offline = [item.name for item in db.query(Collector).filter(or_(
        Collector.last_heartbeat_at.is_(None), Collector.last_heartbeat_at < utc_now() - timedelta(seconds=60),
    ))]
    return {"alerts": [alert_summary(*row) for row in rows[:limit]], "has_more": len(rows) > limit,
            "offline_collectors": offline, **({"total": total} if include_count else {})}


def event_payload(event: Event):
    return redact({"id": event.id, "timestamp": iso(event.timestamp), "device_id": event.device_id,
                   "type": event.event_type, "severity": event.severity, "title": event.title,
                   "message": event.message, "details": event.details})


@router.get("/events")
def list_incident_events(
    type: str | None = None, device_id: int | None = None, severity: str | None = None,
    errors_only: bool = False, hide_connection_noise: bool = False,
    start: AwareDatetime | None = None, end: AwareDatetime | None = None,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
    _: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    query = db.query(Event)
    if hide_connection_noise:
        routine = or_(func.lower(Event.event_type).in_(CONNECTION_EVENT_TYPES), connection_noise(Event.title))
        query = query.filter(or_(Event.event_type.in_(("link_down", "device_offline", "uplink_down", "downlink_down")), ~routine))
    if errors_only:
        query = query.filter(Event.severity.in_(("emergency", "alert", "critical", "error", "high")))
    for column, value in ((Event.event_type, type), (Event.device_id, device_id), (Event.severity, severity)):
        if value is not None:
            query = query.filter(column == value)
    if start:
        query = query.filter(Event.timestamp >= start.astimezone(timezone.utc).replace(tzinfo=None))
    if end:
        query = query.filter(Event.timestamp <= end.astimezone(timezone.utc).replace(tzinfo=None))
    rows = query.order_by(Event.timestamp.desc(), Event.id.desc()).offset(offset).limit(limit + 1).all()
    return {"events": [event_payload(row) for row in rows[:limit]], "has_more": len(rows) > limit}


@router.get("/zabbix")
def zabbix_incident_feed(
    kind: Literal["problems", "events"] = "problems",
    category: Literal["critical", "device_offline", "link_down", "other"] | None = None,
    q: str = Query("", max_length=120), severity: str | None = None, status: str | None = None,
    type: str | None = None, device_id: int | None = None, site_id: int | None = None,
    start: AwareDatetime | None = None, end: AwareDatetime | None = None, errors_only: bool = False,
    _: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    connection = db.get(ZabbixConnection, 1)
    if connection is None:
        return {"configured": False, "entries": [], "limited": False, "total": 0, "scan_limited": False}
    now = utc_now()
    earliest = naive_utc(start) if start else now - timedelta(days=1)
    if kind == "problems":
        earliest = max(earliest, now - ACTIVE_ALERT_WINDOW)
    latest = naive_utc(end) if end else now
    alert_types = {"availability": "device_offline", "link": "link_down", "rule": "other"}
    if earliest > latest or (type and type not in (alert_types if kind == "problems" else {"zabbix_problem", "zabbix_recovery"})):
        return {"configured": True, "entries": [], "limited": False, "total": 0, "scan_limited": False}
    params: dict[str, Any] = {"source": 0, "object": 0, "sortfield": "eventid", "sortorder": "DESC",
              "time_from": int(earliest.replace(tzinfo=timezone.utc).timestamp()),
              "time_till": int(latest.replace(tzinfo=timezone.utc).timestamp()), "limit": 500}
    with saved_api(db) as api:
        hidden = hidden_host_ids(db, connection.api_url)
        if device_id is not None or site_id is not None or hidden:
            query = db.query(Device.id)
            if device_id is not None:
                query = query.filter(Device.id == device_id)
            if site_id is not None:
                query = query.filter(Device.site_id == site_id)
            inventory = api.rows("host.get", HOST_FIELDS)
            if device_id is not None or site_id is not None:
                selected_ids = {row[0] for row in query}
                matching = local_matches(db, connection.api_url, inventory)
                host_ids = [key for key, ids in matching.items() if key not in hidden and selected_ids.intersection(ids)]
            else:
                host_ids = [str(row["hostid"]) for row in inventory if row.get("hostid") and str(row["hostid"]) not in hidden]
            if not host_ids:
                return {"configured": True, "entries": [], "limited": False, "total": 0, "scan_limited": False}
            params["hostids"] = host_ids
        fields = ["eventid", "name", "severity", "clock", "acknowledged", "suppressed"]
        if kind == "problems":
            records = api.rows("problem.get", {**params, "recent": False, "output": fields})
            event_ids = [row["eventid"] for row in records if row.get("eventid")]
            if event_ids:
                try:
                    metadata = api.rows("event.get", {"eventids": event_ids, "output": ["eventid"],
                                                      "selectHosts": ["name", "host"]})
                except HTTPException:
                    metadata = []
                host_by_event = {str(row.get("eventid")): row.get("hosts") for row in metadata}
            else:
                host_by_event = {}
        else:
            records = api.rows("event.get", {**params, "output": [*fields, "value"], "selectHosts": ["name", "host"]})
            host_by_event = {str(row.get("eventid")): row.get("hosts") for row in records}
        dismissed = [] if kind == "events" else db.query(ZabbixAlertDismissal.start, ZabbixAlertDismissal.end).filter(
            ZabbixAlertDismissal.source_key == source_key(connection.api_url),
            ZabbixAlertDismissal.end >= earliest, ZabbixAlertDismissal.start <= latest,
        ).all()
        entries: list[dict[str, str]] = []
        for row in records:
            event_id = str(row.get("eventid"))
            stamp = str(row.get("clock"))
            if not (event_id.isascii() and event_id.isdecimal() and stamp.isascii() and stamp.isdecimal() and len(stamp) <= 10):
                continue
            time = datetime.fromtimestamp(int(stamp), timezone.utc).replace(tzinfo=None)
            if not earliest.replace(microsecond=0) <= time <= latest or any(left <= time <= right for left, right in dismissed):
                continue
            title = visible(row.get("name"), api.token)
            group = "device_offline" if ZABBIX_OFFLINE.search(title) else "link_down" if ZABBIX_LINK.search(title) else "other"
            if group == "other" and ZABBIX_NOISE.search(title):
                continue
            if category and (group == "other" if category == "critical" else category != group):
                continue
            if kind == "problems" and type and group != alert_types.get(type):
                continue
            recovered = kind == "events" and str(row.get("value")) == "0"
            state = ("resolved" if recovered else "suppressed" if str(row.get("suppressed")) == "1"
                     else "acknowledged" if str(row.get("acknowledged")) == "1" else "active")
            level = ZABBIX_SEVERITY.get(str(row.get("severity")), "info")
            event_type = "zabbix_recovery" if recovered else "zabbix_problem"
            hosts = host_by_event.get(event_id)
            names = ", ".join(visible(item.get("name") or item.get("host"), api.token)
                              for item in hosts if isinstance(item, dict)) if isinstance(hosts, list) else ""
            if (status and status != state) or (severity and severity != level) or (kind == "events" and type and type != event_type):
                continue
            if errors_only and (recovered or level not in {"average", "high", "critical"}):
                continue
            if q and q.casefold() not in f"{title} {names}".casefold():
                continue
            entries.append({"id": event_id, "title": title, "device": names, "timestamp": time.replace(tzinfo=timezone.utc).isoformat(),
                            "status": state, "severity": level, "category": group})
    entries.sort(key=lambda row: (row["timestamp"], int(row["id"])), reverse=True)
    return {"configured": True, "entries": entries[:100], "limited": len(entries) > 100 or len(records) >= 500,
            "total": len(entries), "scan_limited": len(records) >= 500}


@router.get("/alerts/{alert_id}")
def incident_detail(alert_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = joined_alerts(db).filter(Alert.id == alert_id).first()
    if row is None:
        raise HTTPException(404, "Alert not found")
    alert, incident, device, check, site, category = row
    start = alert.triggered_at - timedelta(minutes=5)
    end = (alert.resolved_at or utc_now()) + timedelta(minutes=5)
    changes = db.query(IncidentTimeline).filter_by(alert_id=alert_id).order_by(
        IncidentTimeline.timestamp.desc(), IncidentTimeline.id.desc(),
    ).limit(200).all()
    events = db.query(Event).filter(Event.device_id == device.id, Event.check_id == alert.check_id,
                                   Event.timestamp.between(start, end)).order_by(Event.timestamp.desc()).limit(30)
    metric_keys = {"icmp": "icmp.rtt_avg", "http": "http.response_time_ms", "https": "http.response_time_ms",
                   "tcp": "tcp.connect_time_ms", "snmp": "snmp.available"}
    metric = metric_keys.get(check.check_type) if check else None
    samples = db.query(MetricRaw.timestamp, MetricRaw.value).filter(
        MetricRaw.check_id == check.id, MetricRaw.metric_key == metric, MetricRaw.timestamp.between(start, end),
    ).order_by(MetricRaw.timestamp.desc()).limit(600).all() if check and metric else []
    logs = []
    logs_available = False
    try:
        logs = [redact({"id": log["_id"], "timestamp": log["timestamp"].isoformat(), "source": log["source"],
                        "message": log["message"], "severity": log["severity"]})
                for log in log_collection().find({"source": device.ip, "timestamp": {
                    "$gte": start.replace(tzinfo=timezone.utc), "$lte": end.replace(tzinfo=timezone.utc),
                }}).sort("timestamp", -1).limit(30)]
        logs_available = True
    except (HTTPException, PyMongoError):
        pass
    windows = db.query(MaintenanceWindow).filter(
        MaintenanceWindow.start_time <= end, MaintenanceWindow.end_time >= start,
        or_(MaintenanceWindow.scope_type == "all",
            (MaintenanceWindow.scope_type == "device") & (MaintenanceWindow.scope_id == device.id),
            (MaintenanceWindow.scope_type == "site") & (MaintenanceWindow.scope_id == device.site_id),
            (MaintenanceWindow.scope_type == "group") & (MaintenanceWindow.scope_id == device.group_id)),
    ).order_by(MaintenanceWindow.start_time).limit(30)
    deliveries = db.query(IncidentDelivery).filter_by(alert_id=alert_id).order_by(IncidentDelivery.id.desc()).limit(30)
    return {
        **alert_summary(alert, incident, device, check, site, category), "message": redact(alert.message),
        "timeline": [redact({"id": item.id, "timestamp": iso(item.timestamp), "kind": item.kind,
                             "actor": item.actor, "message": item.message}) for item in reversed(changes)],
        "events": [event_payload(event) for event in events], "logs": logs, "logs_available": logs_available,
        "metric": metric, "samples": [{"timestamp": iso(sample.timestamp), "value": sample.value} for sample in reversed(samples)],
        "maintenance": [{"name": window.name, "start": iso(window.start_time), "end": iso(window.end_time)} for window in windows],
        "notifications": [{"id": item.id, "channel": "webhook", "event": item.event, "status": item.status,
                           "outcome": item.outcome, "timestamp": iso(item.delivered_at or item.next_attempt)} for item in deliveries],
    }


class Action(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)
    action: Literal["acknowledge", "resolve", "comment", "silence"]
    comment: str = Field("", max_length=500)
    minutes: int = Field(15, ge=1, le=1440)


class AlertPeriod(BaseModel):
    start: AwareDatetime
    end: AwareDatetime


@router.post("/alerts/dismiss")
def dismiss_alerts(payload: AlertPeriod, user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    start, end = naive_utc(payload.start), naive_utc(payload.end)
    if end < start:
        raise HTTPException(422, "End time must be on or after start time")
    result = cast(CursorResult, db.execute(update(Alert).where(
        Alert.triggered_at.between(start, end), alert_not_dismissed(),
    ).values(dismissed_episode_at=Alert.triggered_at)))
    removed = result.rowcount
    connection = db.get(ZabbixConnection, 1)
    external = connection is not None and start <= utc_now()
    if connection and external:
        key = source_key(connection.api_url)
        end_bound = min(end, utc_now()).replace(microsecond=0)
        start_bound = start.replace(microsecond=0)
        if not db.query(ZabbixAlertDismissal.id).filter_by(source_key=key, start=start_bound, end=end_bound).first():
            db.add(ZabbixAlertDismissal(source_key=key, start=start_bound, end=end_bound))
    if removed or external:
        db.add(AuditLog(user_id=user.id, action="alerts.dismissed_by_period", entity_type="alert",
                        details={"start": iso(start), "end": iso(end), "removed": removed}))
    db.commit()
    if removed or external:
        notify_change()
    return {"removed": removed, **({"zabbix_range_applied": True} if external else {})}


@router.post("/actions")
def apply_action(payload: Action, user: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    ids = set(payload.ids)
    alerts = db.query(Alert).filter(Alert.id.in_(ids)).order_by(Alert.id).with_for_update().all()
    if len(alerts) != len(ids):
        raise HTTPException(404, "One or more alerts were not found")
    if payload.action == "comment" and not payload.comment.strip():
        raise HTTPException(422, "Enter a comment")
    now = utc_now()
    count = 0
    for alert in alerts:
        if payload.action == "acknowledge" and alert.status != "active":
            continue
        if payload.action in {"silence", "resolve"} and alert.status == "resolved":
            continue
        incident = db.get(Incident, alert.id)
        if incident is None:
            incident = Incident(alert_id=alert.id, correlation_key=f"legacy:{alert.id}", occurrence_count=1)
            db.add(incident)
        kind: str = payload.action
        if kind == "acknowledge":
            alert.status = "acknowledged"
            incident.acknowledged_at = now
            db.add(Acknowledgement(alert_id=alert.id, user_id=user.id, comment=payload.comment or "Acknowledged"))
            kind = "acknowledged"
        elif kind == "resolve":
            if not incident.suppression and not (incident.silenced_until and incident.silenced_until > now):
                queue_delivery(db, alert, "resolved", now)
            alert.status = "resolved"
            alert.resolved_at = now
            incident.suppression = None
            kind = "resolved"
        elif kind == "silence":
            incident.silenced_until = now + timedelta(minutes=payload.minutes)
        timeline(db, alert.id, now, kind, payload.comment or (
            f"Silenced for {payload.minutes} minutes" if kind == "silence" else kind.capitalize()), user.username)
        count += 1
    db.commit()
    if count:
        notify_change()
    return {"updated": count}


class SavedFilter(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    values: dict[str, str] = Field(max_length=12)


@router.get("/filters")
def saved_filters(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": item.id, "name": item.name, "values": item.values}
            for item in db.query(IncidentFilter).filter_by(user_id=user.id).order_by(IncidentFilter.id).limit(50)]


@router.post("/filters", status_code=201)
def save_filter(payload: SavedFilter, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if any(len(value) > 120 for value in payload.values.values()):
        raise HTTPException(422, "Filter value is too long")
    if db.query(IncidentFilter).filter_by(user_id=user.id).count() >= 50:
        raise HTTPException(409, "Delete a saved filter before adding another")
    item = IncidentFilter(user_id=user.id, name=payload.name, values=payload.values)
    db.add(item)
    db.commit()
    return {"id": item.id}


@router.delete("/filters/{filter_id}", status_code=204)
def delete_filter(filter_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(IncidentFilter).filter_by(id=filter_id, user_id=user.id).delete():
        raise HTTPException(404, "Filter not found")
    db.commit()


class Parent(BaseModel):
    parent_id: int | None = Field(default=None, gt=0)


@router.put("/parents/{device_id}", status_code=204)
def set_parent(device_id: int, payload: Parent, _: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    if db.get(Device, device_id) is None or (payload.parent_id and db.get(Device, payload.parent_id) is None):
        raise HTTPException(404, "Device not found")
    existing = db.get(IncidentParent, device_id)
    if payload.parent_id is None:
        if existing:
            db.delete(existing)
    else:
        seen = {device_id}
        ancestor: int | None = payload.parent_id
        while ancestor is not None:
            if ancestor in seen:
                raise HTTPException(422, "Device dependencies cannot form a cycle")
            seen.add(ancestor)
            link = db.get(IncidentParent, ancestor)
            ancestor = link.parent_id if link else None
        if existing:
            existing.parent_id = payload.parent_id
        else:
            db.add(IncidentParent(device_id=device_id, parent_id=payload.parent_id))
    db.commit()
