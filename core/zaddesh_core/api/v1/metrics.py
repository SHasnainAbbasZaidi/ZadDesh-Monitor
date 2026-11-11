from __future__ import annotations

import datetime
import hashlib
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, aliased
from sqlalchemy import BigInteger, and_, case, cast, extract, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from zaddesh_core.database import get_db
from zaddesh_core.models.metrics import MetricRaw
from zaddesh_core.models.infrastructure import Collector, Device
from zaddesh_core.models.alerts import Alert, Event
from zaddesh_core.config import settings
from zaddesh_core.incidents import record_availability
from zaddesh_core.models.checks import Check, CheckState
from zaddesh_core.models.auth import User
from zaddesh_core.schemas.metric import (
    MetricIngestBatch,
    MetricQueryRequest,
    MetricQueryResponse,
    MetricSeries,
    MetricSeriesPoint,
    ProbeLatencyChannel,
    ProbeLatencyDevice,
    ProbeLatencyResponse,
)
from zaddesh_core.api.deps import get_current_user, require_role, verify_collector_token

from zaddesh_core.api.v1.stream import notify_change

router = APIRouter(prefix="/metrics", tags=["Metrics & Ingestion"])


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
def ingest_metrics(
    payload: MetricIngestBatch,
    authorized: Collector | None = Depends(verify_collector_token),
    db: Session = Depends(get_db),
):
    """
    Ingest a batch of metrics from the Rust collector or push agents.
    Automatically records raw metrics, refreshes device last_seen, and updates check status.
    """
    if authorized is not None and payload.collector_id != authorized.id:
        raise HTTPException(403, "Collector identity does not match payload")
    if len(payload.points) > settings.collector.batch_size:
        raise HTTPException(413, "Collector batch limit exceeded")
    if not payload.points:
        return {"ingested": 0, "status": "empty"}

    ids = {point.device_id for point in payload.points}
    devices = {row.id: row for row in db.query(Device).filter(Device.id.in_(ids)).all()}
    if ids != devices.keys():
        raise HTTPException(422, "Unknown device in batch")
    if authorized is not None and authorized.site_id is not None:
        if any(device.site_id != authorized.site_id for device in devices.values()):
            raise HTTPException(403, "Device is outside the collector site")

    check_ids = {point.check_id for point in payload.points if point.check_id is not None}
    checks = {row.id: row for row in db.query(Check).filter(Check.id.in_(check_ids)).all()}
    availability = {"icmp": "icmp.availability", "tcp": "tcp.available", "snmp": "snmp.available",
                    "http": "http.available", "https": "http.available"}
    observations: dict[int, tuple[datetime.datetime, bool]] = {}
    incident_samples: dict[int, dict[datetime.datetime, bool]] = {}
    records = []
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    for point in payload.points:
        check = checks.get(point.check_id) if point.check_id is not None else None
        if point.check_id is not None and (check is None or check.device_id != point.device_id):
            raise HTTPException(422, "Check does not belong to the device")
        timestamp = utc_naive(point.timestamp)
        fingerprint = hashlib.sha256(json.dumps([
            point.device_id, point.check_id, point.metric_key, timestamp.isoformat(), point.value, point.tags,
        ], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        records.append(dict(fingerprint=fingerprint, device_id=point.device_id, check_id=point.check_id,
                            metric_key=point.metric_key, timestamp=timestamp,
                            value=point.value, tags=point.tags))
        if check and check.is_enabled and availability.get(check.check_type) == point.metric_key:
            if settings.features.incident_workspace:
                samples = incident_samples.setdefault(check.id, {})
                samples[timestamp] = samples.get(timestamp, True) and point.value == 1.0
            previous = observations.get(check.id)
            if previous is None or timestamp > previous[0]:
                observations[check.id] = (timestamp, point.value == 1.0)

    dialect = db.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise HTTPException(501, "Ingestion requires SQLite or PostgreSQL")
    insert = sqlite_insert if dialect == "sqlite" else pg_insert
    inserted = db.connection().execute(
        insert(MetricRaw).on_conflict_do_nothing(index_elements=["fingerprint"]), records,
    ).rowcount
    for device in devices.values():
        device.last_seen = now

    states = db.query(CheckState).filter(CheckState.check_id.in_(observations)).order_by(
        CheckState.check_id
    ).with_for_update().all()
    transition_ids = [
        state.check_id for state in states
        if (state.last_check_time is None or observations[state.check_id][0] > state.last_check_time)
        and ((not observations[state.check_id][1] and state.current_status != "critical")
             or (observations[state.check_id][1] and state.current_status == "critical"))
    ]
    open_alerts = {
        (alert.check_id, alert.title): alert
        for alert in db.query(Alert).filter(
            Alert.check_id.in_(transition_ids), Alert.rule_id.is_(None),
            Alert.status.in_(("active", "acknowledged")),
        ).all()
    } if transition_ids else {}
    touched = set()
    for state in states:
        timestamp, ok = observations[state.check_id]
        if state.last_check_time is not None and timestamp <= state.last_check_time:
            continue  # A late sample may be queried but must not rewind current health.
        check = checks[state.check_id]
        if settings.features.incident_workspace:
            previous_status = state.current_status
            for at, available in sorted(incident_samples[state.check_id].items()):
                if state.last_check_time is None or at > state.last_check_time:
                    record_availability(db, check, devices[check.device_id], previous_status, available, at)
                    previous_status = "ok" if available else "critical"
                    ok = available
        touched.add(check.device_id)
        new_status = "ok" if ok else "critical"
        state.last_check_time = timestamp
        state.consecutive_failures = 0 if ok else state.consecutive_failures + 1
        state.message = "Probe passed" if ok else "Probe failed"
        if state.current_status == new_status:
            continue
        state.current_status = new_status
        state.last_change_time = timestamp
        if settings.features.incident_workspace:
            continue
        title = f"Availability: {check.name}"
        open_alert = open_alerts.get((check.id, title))
        if not ok and open_alert is None:
            db.add(Alert(device_id=check.device_id, check_id=check.id, title=title,
                         message="Probe failed", severity="critical", triggered_at=timestamp))
        if ok and open_alert is not None:
            open_alert.status = "resolved"
            open_alert.resolved_at = timestamp
        db.add(Event(device_id=check.device_id, check_id=check.id, event_type="state_change",
                     severity="info" if ok else "critical", title=title,
                     message=state.message, timestamp=timestamp))

    if touched:
        db.flush()
        summary: dict[int, set[str]] = {device_id: set() for device_id in touched}
        for device_id, status_name in db.query(Check.device_id, CheckState.current_status).join(
            CheckState, CheckState.check_id == Check.id,
        ).filter(Check.device_id.in_(touched), Check.is_enabled.is_(True)):
            summary[device_id].add(status_name)
        for device_id, statuses in summary.items():
            device = devices[device_id]
            if device.is_monitored:
                device.status = ("warning" if "ok" in statuses else "down") if "critical" in statuses else (
                    "up" if "ok" in statuses else "unknown"
                )
    if settings.features.incident_workspace and settings.features.event_retention_days:
        db.query(Event).filter(Event.timestamp < now - datetime.timedelta(
            days=settings.features.event_retention_days,
        )).delete()
    db.commit()
    notify_change()
    return {"ingested": inserted, "duplicates": len(records) - inserted, "status": "success"}


def utc_naive(value: datetime.datetime) -> datetime.datetime:
    return value.astimezone(datetime.timezone.utc).replace(tzinfo=None) if value.tzinfo else value


PROBE_LATENCY = {
    "icmp": (("icmp",), "icmp.rtt_avg", "icmp.availability"),
    "http": (("http", "https"), "http.response_time_ms", "http.available"),
    "tcp": (("tcp",), "tcp.connect_time_ms", "tcp.available"),
}


@router.get("/probe-latency", response_model=ProbeLatencyResponse)
def get_probe_latency(
    protocol: Literal["icmp", "http", "tcp"] = "icmp",
    minutes: int = Query(default=60, ge=1, le=1440),
    device_id: int | None = Query(default=None, gt=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProbeLatencyResponse:
    check_types, latency_key, availability_key = PROBE_LATENCY[protocol]
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(minutes=minutes)
    devices = [ProbeLatencyDevice(id=row.id, name=row.name, ip=row.ip, status=row.status)
               for row in db.query(Device.id, Device.name, Device.ip, Device.status).order_by(Device.name, Device.id)]
    check_selection = (
        select(func.min(Check.id).label("check_id"))
        .join(Device, Device.id == Check.device_id)
        .where(Check.is_enabled.is_(True), Device.is_monitored.is_(True), Check.check_type.in_(check_types))
        .group_by(Check.device_id)
    )
    if device_id is not None:
        check_selection = check_selection.where(Check.device_id == device_id)
    chosen = check_selection.cte("selected_probes")
    checks = db.execute(
        select(Check.id, Check.device_id, Check.name, Check.interval_seconds, CheckState.current_status)
        .join(chosen, chosen.c.check_id == Check.id)
        .outerjoin(CheckState, CheckState.check_id == Check.id)
    )
    channels = {
        row.id: ProbeLatencyChannel(
            device_id=row.device_id, check_name=row.name, probe_down=row.current_status == "critical",
            interval_seconds=row.interval_seconds,
        )
        for row in checks
    }
    if not channels:
        return ProbeLatencyResponse(devices=devices, channels=[], start=start, end=end, bucket_seconds=0)
    buckets_per_device = max(1, min(240, 60_000 // len(channels)))
    bucket_seconds = (minutes * 60 + buckets_per_device - 1) // buckets_per_device
    for configured_channel in channels.values():
        configured_channel.interval_seconds = max(configured_channel.interval_seconds, bucket_seconds)

    epoch = (func.strftime("%s", MetricRaw.timestamp) if db.get_bind().dialect.name == "sqlite"
             else extract("epoch", MetricRaw.timestamp))
    bucket = cast(epoch, BigInteger) // bucket_seconds
    failure = aliased(MetricRaw)
    failed_at_timestamp = select(failure.id).where(
        failure.check_id == MetricRaw.check_id, failure.timestamp == MetricRaw.timestamp,
        failure.metric_key == availability_key, failure.value == 0,
    ).exists()
    # Keep the peak successful latency and the first failure in each time bucket.
    # Separate streams preserve outages without charting a failed response as latency.
    samples = (
        select(
            MetricRaw.check_id, MetricRaw.metric_key, MetricRaw.timestamp, MetricRaw.value,
            func.row_number().over(
                partition_by=(MetricRaw.check_id, MetricRaw.metric_key, bucket),
                order_by=(
                    case((MetricRaw.metric_key == latency_key, MetricRaw.value), else_=0).desc(),
                    MetricRaw.timestamp, MetricRaw.id,
                ),
            ).label("sample_rank"),
        )
        .join(chosen, chosen.c.check_id == MetricRaw.check_id)
        .where(
            MetricRaw.timestamp.between(utc_naive(start), utc_naive(end)),
            or_(
                and_(MetricRaw.metric_key == latency_key, ~failed_at_timestamp),
                and_(MetricRaw.metric_key == availability_key, MetricRaw.value == 0),
            ),
        ).subquery()
    )
    for row in db.execute(select(samples).where(samples.c.sample_rank == 1).order_by(samples.c.timestamp)):
        channel = channels.get(row.check_id)
        if channel is None:
            continue
        timestamp = row.timestamp.replace(tzinfo=datetime.timezone.utc)
        if row.metric_key == latency_key:
            channel.latency.append(MetricSeriesPoint(timestamp=timestamp, value=row.value))
        else:
            channel.failures.append(timestamp)
    return ProbeLatencyResponse(devices=devices, channels=list(channels.values()), start=start, end=end,
                                bucket_seconds=bucket_seconds)


@router.post("/query", response_model=MetricQueryResponse)
def query_metrics(
    payload: MetricQueryRequest,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Query time-series points for given device and metric keys."""
    results = []

    for key in payload.metric_keys:
        q = db.query(MetricRaw.timestamp, MetricRaw.value).filter(
            MetricRaw.device_id == payload.device_id,
            MetricRaw.metric_key == key,
            MetricRaw.timestamp >= utc_naive(payload.start_time),
            MetricRaw.timestamp <= utc_naive(payload.end_time),
        )
        if payload.check_id:
            q = q.filter(MetricRaw.check_id == payload.check_id)

        rows = q.order_by(MetricRaw.timestamp.asc()).all()
        points = [MetricSeriesPoint(timestamp=ts, value=val) for ts, val in rows]

        results.append(
            MetricSeries(
                metric_key=key,
                device_id=payload.device_id,
                check_id=payload.check_id,
                points=points,
            )
        )

    return MetricQueryResponse(series=results)


@router.get("/latest")
def get_latest_metrics(
    device_id: int,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Get the most recent metric values for a device."""
    subq = (
        db.query(
            MetricRaw.metric_key,
            func.max(MetricRaw.timestamp).label("max_ts"),
        )
        .filter(MetricRaw.device_id == device_id)
        .group_by(MetricRaw.metric_key)
        .subquery()
    )

    rows = (
        db.query(MetricRaw)
        .join(
            subq,
            (MetricRaw.metric_key == subq.c.metric_key)
            & (MetricRaw.timestamp == subq.c.max_ts),
        )
        .filter(MetricRaw.device_id == device_id)
        .all()
    )

    return [
        {
            "metric_key": r.metric_key,
            "value": r.value,
            "timestamp": r.timestamp,
            "tags": r.tags,
            "check_id": r.check_id,
        }
        for r in rows
    ]

