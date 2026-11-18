from __future__ import annotations

import datetime
import json
from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, PositiveInt, StringConstraints
from sqlalchemy import func, update

from zaddesh_core.database import get_db
from zaddesh_core.config import settings
from zaddesh_core.models.infrastructure import Device, DeviceCredential, Site, Group
from zaddesh_core.models.checks import Check, CheckState
from zaddesh_core.models.auth import AuditLog, User
from zaddesh_core.models.topology import TopologyNode
from zaddesh_core.models.zabbix import ZabbixConnection, ZabbixHiddenHost
from zaddesh_core.api.v1.stream import notify_change
from zaddesh_core.api.v1.zabbix import hidden_host_ids, source_key
from zaddesh_core.schemas.device import (
    DeviceCreate,
    DeviceUpdate,
    DeviceRead,
    DeviceDetail,
    DeviceStats,
    SiteCreate,
    SiteRead,
    GroupCreate,
    GroupRead,
)
from zaddesh_core.api.deps import get_current_user, require_role
from zaddesh_core.security import allows_secret_transport, encrypt_secret

router = APIRouter(prefix="", tags=["Infrastructure & Devices"])


# --- SITES ---
@router.get("/sites", response_model=List[SiteRead])
def list_sites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(Site).order_by(Site.name.asc()).all()


@router.post("/sites", response_model=SiteRead, status_code=status.HTTP_201_CREATED)
def create_site(
    payload: SiteCreate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    existing = db.query(Site).filter((Site.name == payload.name) | (Site.slug == payload.slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Site name or slug already exists")
    site = Site(**payload.model_dump())
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


# --- GROUPS ---
@router.get("/groups", response_model=List[GroupRead])
def list_groups(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    return db.query(Group).order_by(Group.name.asc()).all()


@router.post("/groups", response_model=GroupRead, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    existing = db.query(Group).filter(Group.slug == payload.slug).first()
    if existing:
        raise HTTPException(status_code=400, detail="Group slug already exists")
    group = Group(**payload.model_dump())
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


# --- DEVICES ---
@router.get("/devices/stats", response_model=DeviceStats)
def get_device_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    devices = db.query(Device.status, func.count(Device.id)).group_by(Device.status).all()
    stats = {
        "total_devices": sum(count for _, count in devices),
        "up": 0,
        "down": 0,
        "warning": 0,
        "unknown": 0,
        "unmanaged": 0,
    }
    for st, count in devices:
        s = st.lower()
        if s in stats:
            stats[s] += count
        else:
            stats["unknown"] += count
    return DeviceStats(**stats)


@router.get("/devices", response_model=List[DeviceRead])
def list_devices(
    query: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    site_id: Optional[int] = None,
    group_id: Optional[int] = None,
    snmp_only: bool = False,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Device)
    if snmp_only:
        q = q.filter(Device.id.in_(db.query(Check.device_id).filter(Check.check_type == "snmp", Check.is_enabled.is_(True))))
    if query:
        term = f"%{query.strip()}%"
        q = q.filter((Device.name.ilike(term)) | (Device.ip.ilike(term)) | (Device.hostname.ilike(term)))
    if status_filter:
        q = q.filter(Device.status == status_filter.lower())
    if site_id:
        q = q.filter(Device.site_id == site_id)
    if group_id:
        q = q.filter(Device.group_id == group_id)

    return q.order_by(Device.name.asc(), Device.id.asc()).offset(offset).limit(limit).all()


@router.post("/devices", response_model=DeviceRead, status_code=status.HTTP_201_CREATED)
def create_device(
    payload: DeviceCreate,
    request: Request,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    snmp = payload.initial_snmp
    if payload.collection_mode == "on_demand" and snmp is None:
        raise HTTPException(422, "On-demand collection requires an SNMP interface")
    dns_name = (payload.hostname or "").strip()
    if snmp and snmp.connect_to == "dns" and (not dns_name or len(dns_name) > 255):
        raise HTTPException(422, "Enter a DNS name to connect to the SNMP interface by DNS")
    encrypted_credentials = None
    if snmp and snmp.credentials is not None:
        if not allows_secret_transport(request):
            raise HTTPException(403, "Use HTTPS to submit SNMP credentials remotely")
        if not settings.security.encryption_key:
            raise HTTPException(503, "SNMP credential storage requires ZADDESH_SECURITY__ENCRYPTION_KEY")
        credentials = {
            field: secret.get_secret_value()
            for field, secret in snmp.credentials
            if secret is not None
        }
        encrypted_credentials = encrypt_secret(json.dumps(credentials))
    device_data = payload.model_dump(exclude={"initial_check_types", "initial_snmp", "initial_tcp_port"})
    device = Device(**device_data)
    device.status = "unknown"
    db.add(device)
    db.flush()

    # Automatically provision initial checks if requested
    check_types: list[str] = list(dict.fromkeys(payload.initial_check_types))
    if snmp is not None:
        check_types.append("snmp")
    for check_type in check_types:
        params: dict[str, object] = {}
        interval_seconds = 60
        target = device.ip
        if check_type == "icmp":
            params = {"count": 3, "timeout_ms": 2000}
        elif check_type == "tcp" and payload.initial_tcp_port != 80:
            params = {"port": payload.initial_tcp_port}
        elif check_type == "https" and device.hostname:
            target = device.hostname
        elif check_type == "snmp" and snmp is not None:
            params = snmp.params.model_dump(exclude_none=True)
            interval_seconds = snmp.interval_seconds
            if snmp.connect_to == "dns":
                target = dns_name
            if encrypted_credentials is not None:
                credential = DeviceCredential(
                    device_id=device.id, credential_type="snmp", encrypted_data=encrypted_credentials,
                )
                db.add(credential)
                db.flush()
                params["stored_credential_id"] = credential.id
        suffix = f" - {check_type.upper()}"
        chk = Check(
            device_id=device.id,
            name=f"{device.name[:128 - len(suffix)]}{suffix}",
            check_type=check_type,
            target=target,
            params=params,
            interval_seconds=interval_seconds,
            timeout_seconds=5,
            retries=3,
            is_enabled=True,
        )
        db.add(chk)
        db.flush()

        state = CheckState(
            check_id=chk.id,
            current_status="unknown",
            consecutive_failures=0,
            flap_detected=False,
            message="Provisioned, pending first poll",
        )
        db.add(state)

    db.commit()
    db.refresh(device)
    return device


@router.get("/devices/{device_id}", response_model=DeviceDetail)
def get_device(
    device_id: int,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    checks = db.query(Check).filter(Check.device_id == device.id).all()
    check_count = len(checks)
    checks_up = 0
    checks_down = 0

    for chk in checks:
        if chk.state:
            st = chk.state.current_status.lower()
            if st == "ok":
                checks_up += 1
            elif st in ("warning", "critical"):
                checks_down += 1

    site_read = SiteRead.model_validate(device.site) if device.site else None
    group_read = GroupRead.model_validate(device.group) if device.group else None

    return DeviceDetail(
        id=device.id,
        name=device.name,
        ip=device.ip,
        hostname=device.hostname,
        site_id=device.site_id,
        group_id=device.group_id,
        status=device.status,
        os=device.os,
        vendor=device.vendor,
        model=device.model,
        uptime_seconds=device.uptime_seconds,
        last_seen=device.last_seen,
        is_monitored=device.is_monitored,
        is_priority=device.is_priority,
        collection_mode="on_demand" if device.collection_mode == "on_demand" else "scheduled",
        created_at=device.created_at,
        updated_at=device.updated_at,
        site=site_read,
        group=group_read,
        check_count=check_count,
        checks_up=checks_up,
        checks_down=checks_down,
    )


@router.put("/devices/{device_id}", response_model=DeviceRead)
def update_device(
    device_id: int,
    payload: DeviceUpdate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        if k in {"is_priority", "collection_mode"} and v is None:
            raise HTTPException(422, "Priority and collection mode require a value")
        if k == "collection_mode" and v == "on_demand" and db.query(Check).filter(Check.device_id == device.id, Check.check_type == "snmp", Check.is_enabled.is_(True)).first() is None:
            raise HTTPException(422, "On-demand collection requires an enabled SNMP interface")
        setattr(device, k, v)
    device.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(device)
    return device


class DeviceRemoval(BaseModel):
    device_ids: list[PositiveInt] = Field(default_factory=list, max_length=1000)
    zabbix_host_ids: list[Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]] = Field(
        default_factory=list, max_length=1000,
    )


@router.post("/devices/bulk-delete")
def bulk_delete_devices(payload: DeviceRemoval, user: User = Depends(require_role(["admin"])),
                        db: Session = Depends(get_db)):
    hidden = 0
    if payload.zabbix_host_ids:
        connection = db.get(ZabbixConnection, 1)
        if connection is None:
            raise HTTPException(409, "The Zabbix connection changed. Refresh the inventory and try again.")
        db.execute(update(ZabbixConnection).where(ZabbixConnection.id == 1).values(id=ZabbixConnection.id))
        new_hosts = set(payload.zabbix_host_ids) - hidden_host_ids(db, connection.api_url)
        db.add_all(ZabbixHiddenHost(source_key=source_key(connection.api_url), host_id=host_id) for host_id in new_hosts)
        hidden = len(new_hosts)
    ids = sorted(set(payload.device_ids))
    deleted = 0
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        db.query(TopologyNode).filter(TopologyNode.device_id.in_(chunk)).delete(synchronize_session=False)
        deleted += db.query(Device).filter(Device.id.in_(chunk)).delete(synchronize_session=False)
    db.add(AuditLog(user_id=user.id, action="devices.bulk_deleted", entity_type="device",
                    details={"deleted": deleted, "zabbix_hidden": hidden}))
    db.commit()
    notify_change()
    return {"deleted": deleted, "zabbix_hidden": hidden}


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    device_id: int,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    db.query(TopologyNode).filter(TopologyNode.device_id == device_id).delete(synchronize_session=False)
    if not db.query(Device).filter(Device.id == device_id).delete(synchronize_session=False):
        raise HTTPException(status_code=404, detail="Device not found")
    db.commit()
    notify_change()

