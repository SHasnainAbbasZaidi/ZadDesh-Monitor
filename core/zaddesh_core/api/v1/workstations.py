from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
import math
from secrets import token_hex
from typing import Annotated, Any, cast

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import or_, update
from sqlalchemy.orm import Session
from sqlalchemy.engine import CursorResult

from zaddesh_core.api.deps import require_role, verify_collector_token
from zaddesh_core.database import get_db
from zaddesh_core.incidents import availability_context, record_availability, redact, utc_now
from zaddesh_core.models import Check, CheckState, Collector, Device, DeviceCredential, Event, MetricRaw, WorkstationInventory
from zaddesh_core.security import allows_secret_transport, decrypt_secret
from zaddesh_core.workstation_logs import save_client_logs
from zaddesh_core.api.v1.stream import notify_change

admin = require_role(["admin"])
router = APIRouter(tags=["SNMP workstation inventory"])
PROCESS = "1.3.6.1.2.1.25.4.2.1"
SERVICE_LINES = "1.3.6.1.4.1.8072.1.3.2.4.1.2.16.122.97.100.100.101.115.104.95.115.101.114.118.105.99.101.115."


def snmp_check(device_id: int, db: Session):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "Device not found")
    check = db.query(Check).filter(Check.device_id == device_id, Check.check_type == "snmp", Check.is_enabled.is_(True)).order_by(Check.id).first()
    if check is None:
        raise HTTPException(409, "This device needs an enabled SNMP check")
    return device, check


def snapshot(row: WorkstationInventory | None):
    status, message = (row.status, row.message) if row else ("never", None)
    expired = utc_now() - timedelta(seconds=90)
    if row and (row.status == "queued" and row.requested_at < expired or row.status == "running" and row.started_at and row.started_at < expired):
        status, message = "failed", "No collector completed this snapshot. Check collector connectivity, site assignment and SNMP access, then retry. Device availability was not changed."
    return {"status": status, "requested_at": row.requested_at if row else None,
            "collected_at": row.collected_at if row else None, "message": message,
            "processes": row.data.get("processes", []) if row else [], "services": row.data.get("services", []) if row else [],
            "processes_available": bool(row and row.data.get("processes_available")),
            "services_available": bool(row and row.data.get("services_available")), "health": row.data.get("health", {}) if row else {},
            "client_logs_available": bool(row and row.data.get("client_logs_available")),
            "client_logs_error": row.data.get("client_logs_error") if row else None}


@router.get("/devices/{device_id}/inventory", dependencies=[Depends(admin)])
def read_inventory(device_id: int, db: Session = Depends(get_db)):
    snmp_check(device_id, db)
    return snapshot(db.get(WorkstationInventory, device_id))


@router.post("/devices/{device_id}/inventory/refresh", status_code=202, dependencies=[Depends(admin)])
def request_inventory(device_id: int, db: Session = Depends(get_db)):
    _, check = snmp_check(device_id, db)
    row = db.get(WorkstationInventory, device_id)
    now = utc_now()
    if row and (row.status == "queued" and row.requested_at > now - timedelta(seconds=90) or row.status == "running" and row.started_at and row.started_at > now - timedelta(seconds=90)):
        return snapshot(row)
    if row is None:
        row = WorkstationInventory(device_id=device_id, check_id=check.id, requested_at=now)
        db.add(row)
    row.check_id, row.status, row.requested_at, row.message = check.id, "queued", now, None
    db.commit()
    return snapshot(row)


@router.post("/collectors/inventory-next")
def next_inventory(request: Request, response: Response, collector: Collector | None = Depends(verify_collector_token), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    if collector is None or not allows_secret_transport(request):
        raise HTTPException(403, "Individual collector authentication over HTTPS or loopback is required")
    now = utc_now()
    pending = or_(WorkstationInventory.status == "queued", (WorkstationInventory.status == "running") & (WorkstationInventory.started_at < now - timedelta(seconds=90)))
    query = db.query(WorkstationInventory, Check, Device).join(Check, WorkstationInventory.check_id == Check.id).join(Device, Check.device_id == Device.id).filter(pending, Check.check_type == "snmp", Check.is_enabled.is_(True))
    if collector.site_id is not None:
        query = query.filter(Device.site_id == collector.site_id)
    selected = query.order_by(WorkstationInventory.requested_at).first()
    if selected is None:
        return Response(status_code=204)
    row, check, device = selected
    lease = token_hex(24)
    if not cast(CursorResult, db.execute(update(WorkstationInventory).where(WorkstationInventory.device_id == device.id, pending).values(status="running", started_at=now, collector_id=collector.id, lease=lease))).rowcount:
        return Response(status_code=204)
    params = dict(check.params)
    credential_id = params.pop("stored_credential_id", None)
    credentials = None
    if credential_id is not None:
        credential = db.query(DeviceCredential).filter_by(id=credential_id, device_id=device.id, credential_type="snmp").first()
        try:
            if credential is None:
                raise ValueError
            credentials = json.loads(decrypt_secret(credential.encrypted_data))
        except (InvalidTag, RuntimeError, ValueError):
            row.status, row.message = "failed", "The stored SNMP credential is unavailable"
            db.commit()
            return Response(status_code=204)
    db.commit()
    return {"run_id": device.id, "lease": lease, "task": {"check_id": check.id, "device_id": device.id, "device_name": device.name,
            "name": "Workstation inventory", "check_type": "snmp", "target": check.target, "params": params, "snmp_credentials": credentials,
            "interval_seconds": check.interval_seconds, "timeout_seconds": check.timeout_seconds, "retries": check.retries}}


class InventoryResult(BaseModel):
    lease: str = Field(min_length=48, max_length=48)
    values: dict[Annotated[str, StringConstraints(pattern=r"^[0-9]+(?:\.[0-9]+)+$", max_length=256)], Annotated[str, StringConstraints(max_length=1024)]] = Field(default_factory=dict, max_length=8000)
    error: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def require_observation(self):
        if self.error and self.values or not self.error and not any(oid.startswith("1.3.6.1.2.1.1.") for oid in self.values):
            raise ValueError("Supply an error or a snapshot containing SNMP system data")
        return self


def parse_inventory(values: dict[str, str]):
    def column(suffix):
        prefix = PROCESS + "." + suffix + "."
        return {oid[len(prefix):]: value for oid, value in values.items() if oid.startswith(prefix)}
    names, paths, states, kinds = column("2"), column("4"), column("7"), column("6")
    statuses = {"1": "Running", "2": "Runnable", "3": "Waiting", "4": "Invalid"}
    processes = [{"pid": int(index), "name": redact(name), "path": redact(paths.get(index, "")), "status": statuses.get(states.get(index, ""), "Unknown"),
                  "kind": {"1": "Unknown", "2": "Operating system", "3": "Driver", "4": "Application"}.get(kinds.get(index, ""), "Unknown")}
                 for index, name in names.items() if index.isdecimal() and len(index) <= 10]
    services = []
    for oid, value in values.items():
        if oid.startswith(SERVICE_LINES) and "\t" in value:
            name, status = value.split("\t", 1)
            if name.strip():
                services.append({"name": redact(name.strip())[:128], "status": redact(status.strip())[:64]})
    loads = [float(value) for oid, value in values.items() if oid.startswith("1.3.6.1.2.1.25.3.3.1.2.") and value.isdecimal() and 0 <= int(value) <= 100]
    storage = []
    prefix = "1.3.6.1.2.1.25.2.3.1.3."
    for oid, name in values.items():
        if not oid.startswith(prefix):
            continue
        index = oid[len(prefix):]
        fields = [values.get(f"1.3.6.1.2.1.25.2.3.1.{column}.{index}", "") for column in (4, 5, 6)]
        if not all(value.isdecimal() and len(value) <= 12 for value in fields):
            continue
        allocation, size, used = map(int, fields)
        if allocation <= 0 or size <= 0 or used > size or allocation * size >= 2**63:
            continue
        storage.append({"name": redact(name), "used_bytes": allocation * used, "total_bytes": allocation * size,
                        "utilization": round(used * 100 / size, 2), "type_oid": values.get(f"1.3.6.1.2.1.25.2.3.1.2.{index}", "").lstrip(".")})
    health = {"agent_name": redact(values.get("1.3.6.1.2.1.1.5.0", "")),
              "cpu_load_percent": round(sum(loads) / len(loads), 2) if loads else None, "storage": storage}
    return {"health": health, "processes": sorted(processes, key=lambda item: item["pid"]), "services": sorted(services, key=lambda item: item["name"].casefold()),
            "processes_available": bool(names), "services_available": bool(services)}


@router.post("/collectors/inventory-result/{device_id}")
def accept_inventory(device_id: int, payload: InventoryResult, collector: Collector | None = Depends(verify_collector_token), db: Session = Depends(get_db)):
    row = db.get(WorkstationInventory, device_id)
    if collector is None or row is None or row.collector_id != collector.id or row.lease != payload.lease or row.status != "running":
        raise HTTPException(409, "Inventory lease is unavailable")
    device, check = snmp_check(device_id, db)
    if collector.site_id is not None and device.site_id != collector.site_id:
        raise HTTPException(403, "Device is outside the collector site")
    now = utc_now()
    changes: dict[str, Any] = {"status": "failed" if payload.error else "complete"}
    if not payload.error:
        changes["collected_at"] = now
    claimed = cast(CursorResult, db.execute(update(WorkstationInventory).where(WorkstationInventory.device_id == device_id, WorkstationInventory.lease == payload.lease,
        WorkstationInventory.status == "running").values(**changes))).rowcount
    if not claimed:
        raise HTTPException(409, "Inventory lease changed")
    row.message = redact(payload.error) if payload.error else None
    if not payload.error:
        from zaddesh_core.hardware_identity import apply_identity, snmp_identity
        apply_identity(device, snmp_identity(payload.values))
        previous = row.data or {}
        row.data = parse_inventory(payload.values)
        row.data = {**row.data, **save_client_logs(db, device_id, check.id, payload.values)}
        old_services = {item["name"]: item["status"] for item in previous.get("services", [])}
        for service in row.data["services"]:
            if service["name"] in old_services and old_services[service["name"]] != service["status"]:
                db.add(Event(device_id=device_id, check_id=check.id, event_type="service_state", severity="notice",
                    title="Service status changed", message=f"{service['name']}: {old_services[service['name']]} → {service['status']}", timestamp=now))
    state = db.get(CheckState, check.id)
    if state is None:
        state = CheckState(check_id=check.id)
        db.add(state)
        db.flush()
    ok = not payload.error
    previous_status = state.current_status
    if device.is_priority:
        context = availability_context(db, [check], now, now)
        record_availability(db, check, device, previous_status, ok, now, *context)
    else:
        db.add(Event(device_id=device_id, check_id=check.id, event_type="snmp_inventory", severity="warning" if payload.error else "info",
                     title="SNMP snapshot failed" if payload.error else "SNMP snapshot collected", message=row.message or f"Collected {len(row.data.get('processes', []))} processes and {len(row.data.get('services', []))} services", timestamp=now))
    state.current_status, state.last_check_time = "ok" if ok else "critical", now
    state.last_change_time = now if previous_status != state.current_status else state.last_change_time
    state.message = row.message or "SNMP inventory collected"
    device.status = "up" if ok else "down"
    if ok:
        device.last_seen = now
    db.add(MetricRaw(device_id=device_id, check_id=check.id, metric_key="snmp.available", value=1.0 if ok else 0.0,
                     timestamp=now, tags={"source": "inventory"}, fingerprint=sha256(f"inventory:{device_id}:{payload.lease}".encode()).hexdigest()))
    for oid, raw_value in payload.values.items():
        if oid.startswith("1.3.6.1.2.1.25.3.3.1.2.") or any(oid.startswith(f"1.3.6.1.2.1.25.2.3.1.{column}.") for column in (4, 5, 6)):
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if math.isfinite(value) and value >= 0:
                db.add(MetricRaw(device_id=device_id, check_id=check.id, metric_key="snmp." + oid, value=value, timestamp=now,
                    tags={"source": "inventory"}, fingerprint=sha256(f"inventory:{payload.lease}:{oid}".encode()).hexdigest()))
    db.commit()
    notify_change()
    return {"accepted": True}
