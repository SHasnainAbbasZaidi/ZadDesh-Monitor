from __future__ import annotations

import ipaddress
import ssl
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import httpx
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, SecretStr, StringConstraints
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import require_role
from zaddesh_core.database import get_db
from zaddesh_core.incidents import redact, utc_now
from zaddesh_core.models.auth import AuditLog, User
from zaddesh_core.models.checks import Check
from zaddesh_core.models.infrastructure import Device
from zaddesh_core.models.metrics import MetricRaw
from zaddesh_core.models.zabbix import ZabbixConnection
from zaddesh_core.security import allows_secret_transport, decrypt_secret, encrypt_secret
from zaddesh_core.zabbix import ZabbixAPI, normalize_url

admin = require_role(["admin"])
router = APIRouter(prefix="/zabbix", tags=["Zabbix"], dependencies=[Depends(admin)])
HostID = Annotated[str, StringConstraints(pattern=r"^\d{1,20}$")]
HOST_FIELDS = {"output": ["hostid", "host", "name", "status"],
               "selectInterfaces": ["interfaceid", "ip", "dns", "useip", "main"]}
PROBLEM_FIELDS = ["eventid", "name", "severity", "clock", "acknowledged"]


class ConnectionInput(BaseModel):
    api_url: str = Field(min_length=1, max_length=1900)
    token: SecretStr = Field(min_length=1, max_length=512)


def require_secure(request: Request) -> None:
    if not allows_secret_transport(request):
        raise HTTPException(403, "Use HTTPS or local loopback to save a Zabbix token")


def summary(connection: ZabbixConnection | None) -> dict[str, Any]:
    return {"configured": connection is not None, "api_url": connection.api_url if connection else None,
            "version": connection.version if connection else None}


@contextmanager
def connect(api_url: str, token: str, version: str = ""):
    with httpx.Client(timeout=httpx.Timeout(6, connect=4), verify=ssl.create_default_context(),
                      follow_redirects=False, trust_env=False) as client:
        yield ZabbixAPI(client, normalize_url(api_url), token, version)


@contextmanager
def saved_api(db: Session):
    connection = db.get(ZabbixConnection, 1)
    if connection is None:
        raise HTTPException(409, "Connect Zabbix in Settings first")
    try:
        token = decrypt_secret(connection.encrypted_token)
    except (RuntimeError, ValueError, InvalidTag):
        raise HTTPException(503, "Could not read the stored Zabbix token. Check the core encryption key.") from None
    with connect(connection.api_url, token, connection.version) as api:
        yield api


def visible(value: Any, token: str) -> str:
    if isinstance(value, str):
        return redact(value.replace(token, "[redacted]")[:512])
    return ""


def host_interfaces(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = row.get("interfaces")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


@router.get("/connection")
def connection_status(db: Session = Depends(get_db)):
    return summary(db.get(ZabbixConnection, 1))


@router.put("/connection", dependencies=[Depends(require_secure)])
def save_connection(payload: ConnectionInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    url = str(normalize_url(payload.api_url))
    token = payload.token.get_secret_value().strip()
    if not token or any(not 33 <= ord(char) <= 126 for char in token) or token in url:
        raise HTTPException(422, "Enter a valid Zabbix API token")
    try:
        ciphertext = encrypt_secret(token)
    except RuntimeError:
        raise HTTPException(503, "Configure the core encryption key before saving a Zabbix token") from None
    with connect(url, token) as api:
        version = api.detect_version()
        api.rows("host.get", {"output": ["hostid"], "limit": 1})
        api.rows("problem.get", {"output": ["eventid"], "limit": 1})
        api.rows("item.get", {"output": ["itemid"], "limit": 1})
    connection = db.get(ZabbixConnection, 1)
    if connection is None:
        connection = ZabbixConnection(id=1)
        db.add(connection)
    connection.api_url, connection.encrypted_token, connection.version = url, ciphertext, version
    db.add(AuditLog(user_id=user.id, action="zabbix.connection_saved", entity_type="integration", entity_id="zabbix"))
    db.commit()
    return summary(connection)


@router.delete("/connection", status_code=204)
def remove_connection(user: User = Depends(admin), db: Session = Depends(get_db)):
    connection = db.get(ZabbixConnection, 1)
    if connection:
        db.delete(connection)
        db.add(AuditLog(user_id=user.id, action="zabbix.connection_removed", entity_type="integration", entity_id="zabbix"))
        db.commit()


@router.get("/hosts")
def read_hosts(q: str = Query("", max_length=120), db: Session = Depends(get_db)):
    params: dict[str, Any] = {**HOST_FIELDS, "limit": 101, "sortfield": "host"}
    if term := q.strip():
        params.update(search={"host": term, "name": term}, searchByAny=True)
    with saved_api(db) as api:
        rows = api.rows("host.get", params)
        return {"hosts": [
            {**{key: visible(row.get(key), api.token) for key in HOST_FIELDS["output"]},
             "interfaces": [{key: visible(item.get(key), api.token) for key in HOST_FIELDS["selectInterfaces"]}
                            for item in host_interfaces(row)]}
            for row in rows[:100]
        ], "limited": len(rows) > 100}


@router.get("/problems")
def read_problems(db: Session = Depends(get_db)):
    with saved_api(db) as api:
        rows = api.rows("problem.get", {"output": PROBLEM_FIELDS, "recent": False, "limit": 100,
                                       "sortfield": "eventid", "sortorder": "DESC"})
        return [{key: visible(row.get(key), api.token) for key in PROBLEM_FIELDS} for row in rows[:100]]


def canonical_ip(value: Any) -> str | None:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


@router.get("/validation")
def validate_reachability(device_id: int, host_id: HostID, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "ZadDesh device not found")
    with saved_api(db) as api:
        hosts = api.rows("host.get", {**HOST_FIELDS, "hostids": [host_id], "limit": 1})
        if not hosts:
            raise HTTPException(404, "The selected Zabbix host is unavailable")
        device_ip = canonical_ip(device.ip)
        interfaces = host_interfaces(hosts[0])
        matching = [interface for interface in interfaces if device_ip is not None
                    and str(interface.get("useip")) == "1" and canonical_ip(interface.get("ip")) == device_ip]
        if not matching:
            raise HTTPException(422, "Choose a Zabbix host with the same IP interface as the ZadDesh device")
        items = api.rows("item.get", {"hostids": [host_id], "filter": {"key_": ["icmpping"], "status": "0", "state": "0"},
                                     "output": ["lastvalue", "lastclock", "interfaceid"], "limit": 1})
    if not items or items[0].get("lastvalue") not in ("0", "1"):
        return {"status": "unavailable", "message": "The host has no supported, enabled icmpping item with a result."}
    interface_id = str(items[0].get("interfaceid", "0"))
    if not (any(str(interface.get("interfaceid")) == interface_id for interface in matching)
            or (interface_id == "0" and len(interfaces) == 1)):
        return {"status": "unavailable", "message": "The Zabbix ICMP item uses a different or ambiguous host interface."}
    try:
        zabbix_time = datetime.fromtimestamp(int(items[0]["lastclock"]), timezone.utc).replace(tzinfo=None)
    except (OSError, OverflowError, KeyError, TypeError, ValueError):
        raise HTTPException(502, "Zabbix returned an invalid item timestamp") from None
    now = utc_now()
    if not now - timedelta(minutes=15) <= zabbix_time <= now + timedelta(seconds=30):
        return {"status": "unavailable", "message": "The latest Zabbix ICMP result is stale or its timestamp is ahead of the core clock."}
    interval = timedelta(seconds=120)
    sample_query = db.query(MetricRaw.timestamp, MetricRaw.value).join(Check, Check.id == MetricRaw.check_id).filter(
        MetricRaw.device_id == device.id, MetricRaw.metric_key == "icmp.availability",
        Check.check_type == "icmp", Check.target == device.ip, MetricRaw.value.in_((0, 1)),
        MetricRaw.timestamp.between(zabbix_time - interval, zabbix_time + interval),
    )
    samples = [row for row in (
        sample_query.filter(MetricRaw.timestamp <= zabbix_time).order_by(MetricRaw.timestamp.desc(), MetricRaw.id.desc()).first(),
        sample_query.filter(MetricRaw.timestamp > zabbix_time).order_by(MetricRaw.timestamp.asc(), MetricRaw.id.desc()).first(),
    ) if row is not None]
    if not samples:
        return {"status": "unavailable", "message": "No ZadDesh ICMP sample for this IP was recorded within two minutes of the Zabbix result."}
    sample = min(samples, key=lambda row: abs(row.timestamp - zabbix_time))
    remote_value = int(items[0]["lastvalue"])
    return {"status": "match" if sample.value == remote_value else "mismatch",
            "zabbix": {"up": bool(remote_value), "timestamp": zabbix_time.replace(tzinfo=timezone.utc).isoformat()},
            "zaddesh": {"up": bool(sample.value), "timestamp": sample.timestamp.replace(tzinfo=timezone.utc).isoformat()},
            "skew_seconds": round(abs((sample.timestamp - zabbix_time).total_seconds()), 1)}
