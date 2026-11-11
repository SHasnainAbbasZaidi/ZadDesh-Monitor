from __future__ import annotations

import datetime
import json
import logging
import secrets
from typing import List
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from zaddesh_core.database import get_db
from zaddesh_core.models.infrastructure import Collector, Device, DeviceCredential
from zaddesh_core.models.checks import Check
from zaddesh_core.models.auth import User
from zaddesh_core.schemas.system import (
    CollectorRegister,
    CollectorHeartbeat,
    CollectorRead,
)
from zaddesh_core.security import allows_secret_transport, decrypt_secret, hash_token
from zaddesh_core.api.deps import require_role, verify_collector_bootstrap, verify_collector_token

router = APIRouter(prefix="/collectors", tags=["Collectors & Remote Probes"])
logger = logging.getLogger(__name__)


@router.get("", response_model=List[CollectorRead])
def list_collectors(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """List all registered collector probes."""
    rows = db.query(Collector).order_by(Collector.id.asc()).all()
    expiry = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(seconds=60)
    return [CollectorRead.model_validate(row).model_copy(update={"status": "offline"})
            if row.last_heartbeat_at is None or row.last_heartbeat_at < expiry else row for row in rows]


@router.post("/register", response_model=CollectorRead, status_code=status.HTTP_201_CREATED)
def register_collector(
    payload: CollectorRegister,
    _: None = Depends(verify_collector_bootstrap),
    db: Session = Depends(get_db),
):
    """Register or re-register a collector daemon."""
    token_h = hash_token(payload.auth_token)
    existing = db.query(Collector).filter(Collector.name == payload.name).first()

    now = datetime.datetime.utcnow()
    if existing:
        if not secrets.compare_digest(existing.auth_token_hash, token_h):
            raise HTTPException(status_code=409, detail="Collector already enrolled with a different token")
        existing.version = payload.version
        existing.hostname = payload.hostname
        existing.ip_address = payload.ip_address
        existing.status = "online"
        existing.last_heartbeat_at = now
        db.commit()
        db.refresh(existing)
        return existing

    collector = Collector(
        name=payload.name,
        auth_token_hash=token_h,
        site_id=payload.site_id,
        version=payload.version,
        hostname=payload.hostname,
        ip_address=payload.ip_address,
        status="online",
        last_heartbeat_at=now,
        registered_at=now,
    )
    db.add(collector)
    db.commit()
    db.refresh(collector)
    return collector


@router.post("/heartbeat")
def collector_heartbeat(
    payload: CollectorHeartbeat,
    authorized: Collector | None = Depends(verify_collector_token),
    db: Session = Depends(get_db),
):
    """Heartbeat signal from a collector daemon."""
    collector = db.query(Collector).filter(Collector.id == payload.collector_id).first()
    if not collector:
        raise HTTPException(status_code=404, detail="Collector not found")

    if authorized is not None and authorized.id != payload.collector_id:
        raise HTTPException(status_code=403, detail="Collector identity does not match payload")

    collector.status = payload.status
    collector.last_heartbeat_at = datetime.datetime.utcnow()
    db.commit()
    return {"status": "ok", "ack_time": datetime.datetime.utcnow().isoformat()}


@router.get("/{collector_id}/tasks")
def get_collector_tasks(
    collector_id: int,
    request: Request,
    response: Response,
    authorized: Collector | None = Depends(verify_collector_token),
    db: Session = Depends(get_db),
):
    """Retrieve check tasks scheduled for execution by this collector."""
    if authorized is not None and authorized.id != collector_id:
        raise HTTPException(status_code=403, detail="Collector cannot access another collector’s tasks")
    collector = db.query(Collector).filter(Collector.id == collector_id).first()
    if not collector:
        raise HTTPException(status_code=404, detail="Collector not found")

    # If collector is bound to a site, pull checks for devices in that site; otherwise pull all enabled checks
    q = (
        db.query(Check, Device.ip, Device.name.label("device_name"))
        .join(Device, Check.device_id == Device.id)
        .filter(Check.is_enabled.is_(True), Device.is_monitored.is_(True))
    )
    if collector.site_id:
        q = q.filter(Device.site_id == collector.site_id)

    rows = q.all()
    can_read_secrets = authorized is not None and allows_secret_transport(request)
    credential_ids = {
        credential_id
        for chk, _, _ in rows
        if chk.check_type == "snmp"
        and isinstance(credential_id := (chk.params or {}).get("stored_credential_id"), int)
    }
    credentials = {}
    if can_read_secrets and credential_ids:
        credentials = {
            (item.id, item.device_id): item.encrypted_data
            for item in db.query(DeviceCredential).filter(
                DeviceCredential.id.in_(credential_ids), DeviceCredential.credential_type == "snmp",
            )
        }
    response.headers["Cache-Control"] = "no-store"
    tasks = []
    for chk, ip, dev_name in rows:
        params = dict(chk.params or {})
        credential_id = params.pop("stored_credential_id", None) if chk.check_type == "snmp" else None
        task = {
            "check_id": chk.id,
            "device_id": chk.device_id,
            "device_name": dev_name,
            "name": chk.name,
            "check_type": chk.check_type,
            "target": chk.target or ip,
            "params": params,
            "interval_seconds": chk.interval_seconds,
            "timeout_seconds": chk.timeout_seconds,
            "retries": chk.retries,
        }
        if credential_id is not None:
            encrypted = credentials.get((credential_id, chk.device_id))
            if not encrypted:
                continue
            try:
                task["snmp_credentials"] = json.loads(decrypt_secret(encrypted))
            except (InvalidTag, ValueError, RuntimeError):
                logger.error("Could not decrypt SNMP credentials for check %s", chk.id)
                continue
        tasks.append(task)
    return {"tasks": tasks, "count": len(tasks)}

