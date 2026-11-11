from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
import re
from secrets import compare_digest
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import AwareDatetime, BaseModel, Field
from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from zaddesh_core.api.deps import get_current_user, security_bearer
from zaddesh_core.config import settings
from zaddesh_core.models.auth import User
from zaddesh_core.security import allows_secret_transport
from zaddesh_core.incidents import redact
from zaddesh_core.api.v1.stream import notify_change

router = APIRouter(prefix="/logs", tags=["Infrastructure Logs"])
Severity = Literal["emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"]


class LogEntry(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = Field(min_length=1, max_length=255)
    severity: Severity = "info"
    message: str = Field(min_length=1, max_length=8192)


class LogBatch(BaseModel):
    entries: list[LogEntry] = Field(min_length=1, max_length=100)


@lru_cache(maxsize=1)
def log_collection() -> Collection[dict[str, Any]]:
    uri = settings.logs.mongo_uri
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
            {"$setOnInsert": {**entry.model_dump(exclude={"event_id"}), "received_at": received_at, "expires_at": expires_at}},
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


@router.get("", response_model=list[LogEntry])
def read_logs(
    source: str | None = Query(default=None, max_length=255),
    severity: Severity | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    q: str | None = Query(default=None, min_length=1, max_length=120),
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    _: User = Depends(get_current_user),
) -> list[LogEntry]:
    filters: dict[str, Any] = {key: value for key, value in {"source": source, "severity": severity}.items() if value is not None}
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
    return [LogEntry(event_id=row["_id"], timestamp=row["timestamp"], severity=row["severity"],
                     source=redact(row["source"]) if settings.features.incident_workspace else row["source"],
                     message=redact(row["message"]) if settings.features.incident_workspace else row["message"])
            for row in rows]
