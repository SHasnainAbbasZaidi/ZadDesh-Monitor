from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import re
from secrets import compare_digest
from typing import Any, Literal, cast, get_args
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import AwareDatetime, BaseModel, Field
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import get_current_user, security_bearer
from zaddesh_core.config import settings
from zaddesh_core.models.auth import User
from zaddesh_core.models.alerts import Event
from zaddesh_core.database import get_db
from zaddesh_core.security import allows_secret_transport
from zaddesh_core.incidents import redact
from zaddesh_core.api.v1.stream import notify_change
from zaddesh_core.models.zabbix import ZabbixConnection

router = APIRouter(prefix="/logs", tags=["Infrastructure Logs"])
Severity = Literal["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"]


class LogEntry(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(min_length=1, max_length=255)
    severity: Severity = "info"
    message: str = Field(min_length=1, max_length=8192)
    origin: Literal["zaddesh", "integration"] = "integration"
    provider: str = Field(default="external", min_length=1, max_length=64)
    device_id: int | None = Field(default=None, gt=0)


class LogBatch(BaseModel):
    entries: list[LogEntry] = Field(min_length=1, max_length=100)


@lru_cache(maxsize=1)
def log_collection() -> Collection[dict[str, Any]]:
    uri = settings.logs.mongo_uri or (settings.database.url if settings.database.url.startswith(("mongodb://", "mongodb+srv://")) else None)
    if not uri:
        raise HTTPException(503, "MongoDB log storage is not configured")
    client: MongoClient[dict[str, Any]] = MongoClient(
        uri, tz_aware=True, serverSelectionTimeoutMS=2500, timeoutMS=5000,
    )
    collection = client[settings.logs.database]["entries"]
    try:
        collection.create_index("expires_at", expireAfterSeconds=0)
        collection.create_index([("timestamp", DESCENDING)])
        collection.create_index([("source", ASCENDING), ("timestamp", DESCENDING)])
        collection.create_index([("severity", ASCENDING), ("timestamp", DESCENDING)])
        collection.create_index([("origin", ASCENDING), ("timestamp", DESCENDING)])
        collection.create_index([("provider", ASCENDING), ("timestamp", DESCENDING)])
        collection.create_index([("device_id", ASCENDING), ("timestamp", DESCENDING)])
    except PyMongoError:
        client.close()
        raise
    return collection


@router.post("/ingest", status_code=202)
def ingest_logs(
    payload: LogBatch,
    request: Request,
    authorization: HTTPAuthorizationCredentials | None = Security(security_bearer),
) -> dict[str, int]:
    expected = settings.logs.ingest_token
    if settings.demo_mode or not expected:
        raise HTTPException(503, "Infrastructure log ingestion is disabled")
    if not authorization or not compare_digest(expected.encode(), authorization.credentials.encode()):
        raise HTTPException(401, "Log ingestion token required")
    if not allows_secret_transport(request):
        raise HTTPException(403, "Use HTTPS for remote log ingestion")

    received_at = datetime.now(timezone.utc)
    expires_at = received_at + timedelta(days=settings.logs.retention_days)
    operations = [
        UpdateOne(
            {"_id": str(entry.event_id)},
            {"$setOnInsert": {**entry.model_dump(exclude={"event_id"}), "origin": "integration", "received_at": received_at, "expires_at": expires_at}},
            upsert=True,
        )
        for entry in payload.entries
    ]
    try:
        result = log_collection().bulk_write(operations, ordered=True)
    except PyMongoError:
        raise HTTPException(503, "MongoDB log storage is unavailable") from None
    if settings.features.incident_workspace and result.upserted_count:
        notify_change()
    return {"inserted": result.upserted_count, "received": len(payload.entries)}


@router.get("/status")
def source_status(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    connection = db.get(ZabbixConnection, 1)
    if connection is None:
        return {"zabbix": {"configured": False, "status": "not_configured", "last_poll": None, "error": None}}
    from zaddesh_core.api.v1.zabbix import source_key
    try:
        row = log_collection().database.source_status.find_one({"_id": "zabbix:" + source_key(connection.api_url)})
    except PyMongoError:
        raise HTTPException(503, "MongoDB log storage is unavailable") from None
    return {"zabbix": {"configured": True, "status": row["status"] if row else "waiting", "last_poll": row.get("last_poll") if row else None,
                       "error": row.get("error") if row else None}}


@router.get("", response_model=list[LogEntry])
def read_logs(
    source: str | None = Query(default=None, max_length=255),
    severity: Severity | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    q: str | None = Query(default=None, min_length=1, max_length=120),
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    origin: Literal["zaddesh", "integration"] | None = None,
    provider: str | None = Query(default=None, max_length=64),
    device_id: int | None = Query(default=None, gt=0),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LogEntry]:
    if start and end and start > end:
        raise HTTPException(422, "The start time must precede the end time")
    result: list[LogEntry] = []
    if origin != "integration" and provider in (None, "zaddesh") and source in (None, "ZadDesh"):
        events = db.query(Event)
        if device_id is not None:
            events = events.filter(Event.device_id == device_id)
        if start:
            events = events.filter(Event.timestamp >= start.astimezone(timezone.utc).replace(tzinfo=None))
        if end:
            events = events.filter(Event.timestamp <= end.astimezone(timezone.utc).replace(tzinfo=None))
        if q:
            events = events.filter(or_(Event.title.icontains(q, autoescape=True), Event.message.icontains(q, autoescape=True)))
        if severity:
            levels = {"critical": ("critical", "high"), "info": ("info", "low", "ok")}.get(severity, (severity,))
            events = events.filter(Event.severity.in_(levels))
        for event in events.order_by(Event.timestamp.desc(), Event.id.desc()).limit(limit):
            level = {"high": "critical", "low": "info", "ok": "info"}.get(event.severity, event.severity)
            text = redact(event.title + ("\n" + event.message if event.message and event.message != event.title else ""))
            result.append(LogEntry(event_id=uuid5(NAMESPACE_URL, f"zaddesh:event:{event.id}"), timestamp=event.timestamp.replace(tzinfo=timezone.utc),
                                   source="ZadDesh", severity=cast(Severity, level if level in get_args(Severity) else "info"), message=text[:8192],
                                   origin="zaddesh", provider="zaddesh", device_id=event.device_id))
    if origin == "zaddesh":
        return result
    filters: dict[str, Any] = {key: value for key, value in {"source": source, "severity": severity}.items() if value is not None}
    if provider:
        filters["provider"] = provider
    if device_id is not None:
        filters["device_id"] = device_id
    if start is not None or end is not None:
        filters["timestamp"] = {key: value for key, value in (("$gte", start), ("$lte", end)) if value is not None}
    if q and not settings.features.incident_workspace:
        filters["message"] = {"$regex": re.escape(q), "$options": "i"}
    try:
        cursor = log_collection().find(filters).sort("timestamp", DESCENDING)
        if q and settings.features.incident_workspace:
            # Match the same sanitized text that callers can read.
            rows = []
            for row in cursor.limit(2000):
                if q.casefold() in redact(row["message"]).casefold():
                    rows.append(row)
                if len(rows) == limit:
                    break
        else:
            rows = list(cursor.limit(limit))
    except PyMongoError:
        raise HTTPException(503, "MongoDB log storage is unavailable") from None
    result.extend(LogEntry(event_id=row["_id"], timestamp=row["timestamp"], severity=row["severity"],
                           source=redact(row["source"]), message=redact(row["message"]), origin="integration",
                           provider=row.get("provider", "external"), device_id=row.get("device_id")) for row in rows)
    return sorted(result, key=lambda entry: entry.timestamp, reverse=True)[:limit]
