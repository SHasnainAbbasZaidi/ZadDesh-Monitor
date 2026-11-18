from __future__ import annotations

import ssl
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from time import monotonic
from typing import Annotated, Any, cast

import httpx
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, SecretStr, StringConstraints
from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import get_current_user, require_role
from zaddesh_core.database import get_db
from zaddesh_core.incidents import redact, utc_now
from zaddesh_core.models.auth import AuditLog, User
from zaddesh_core.models.checks import Check, CheckState
from zaddesh_core.models.infrastructure import Device, DeviceCredential
from zaddesh_core.models.metrics import MetricRaw
from zaddesh_core.models.zabbix import ZabbixConnection, ZabbixHiddenHost, ZabbixHostImport
from zaddesh_core.security import allows_secret_transport, decrypt_secret, encrypt_secret
from zaddesh_core.zabbix import ZabbixAPI, normalize_url
from zaddesh_core.hardware_identity import identity, clean, snmp_identity, ENTITY
from zaddesh_core.zabbix_import import canonical_ip, duration, interface_target, prepare_checks, resolve_interface_credentials, target_name

admin = require_role(["admin"])
router = APIRouter(prefix="/zabbix", tags=["Zabbix"], dependencies=[Depends(admin)])
fleet_router = APIRouter(prefix="/zabbix", tags=["Zabbix"], dependencies=[Depends(get_current_user)])
HostID = Annotated[str, StringConstraints(pattern=r"^\d{1,20}$")]
HOST_FIELDS = {"output": ["hostid", "host", "name", "status"],
               "selectInventory": ["vendor", "model", "hardware"],
               "selectInterfaces": ["interfaceid", "ip", "dns", "useip", "main", "available"]}
PROBLEM_FIELDS = ["eventid", "name", "severity", "clock", "acknowledged"]


class ConnectionInput(BaseModel):
    api_url: str = Field(min_length=1, max_length=1900)
    token: SecretStr = Field(min_length=1, max_length=512)


class ProbeHistoryInput(BaseModel):
    host_ids: list[HostID] = Field(min_length=1, max_length=100)


@router.post("/probe-history")
def probe_history(payload: ProbeHistoryInput, db: Session = Depends(get_db)):
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=1)
    ids = set(payload.host_ids)
    channels: dict[str, dict[str, Any]] = {}
    with saved_api(db) as api:
        items = api.rows("item.get", {"hostids": sorted(ids), "output": ["itemid", "hostid", "key_", "delay", "value_type"],
                                       "filter": {"status": "0"}, "search": {"key_": "icmpping"}, "startSearch": True,
                                       "sortfield": "itemid", "limit": 1000})
        candidates: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        for item in items:
            host_id = str(item.get("hostid"))
            key, _, parameters = str(item.get("key_", "")).partition("[")
            item_id = str(item.get("itemid"))
            if host_id not in ids or not (item_id.isascii() and item_id.isdecimal() and len(item_id) <= 20):
                continue
            if key not in {"icmpping", "icmppingsec"} or str(item.get("value_type")) != ("3" if key == "icmpping" else "0"):
                continue
            target = parameters.rstrip("]").split(",", 1)[0].strip('" ')
            candidates.setdefault(host_id, {}).setdefault(target, {}).setdefault(key, item)
        selected = {}
        for host_id, targets in candidates.items():
            _, probes = min(targets.items(), key=lambda entry: ("icmpping" not in entry[1], -len(entry[1]), bool(entry[0]), entry[0]))
            availability = probes.get("icmpping") or probes["icmppingsec"]
            interval = duration(availability.get("delay"))
            channels[host_id] = {"interval_seconds": interval, "latency": [], "successes": [], "failures": []}
            selected.update({str(item["itemid"]): (host_id, key) for key, item in probes.items()})
        for value_type, probe in ((0, "icmppingsec"), (3, "icmpping")):
            item_ids = [item_id for item_id, (_, key) in selected.items() if key == probe]
            if not item_ids:
                continue
            rows = api.rows("history.get", {"output": ["itemid", "clock", "value"], "itemids": item_ids, "history": value_type,
                                             "time_from": int(start.timestamp()), "time_till": int(end.timestamp()),
                                             "sortfield": "clock", "sortorder": "DESC", "limit": 12000})
            for row in rows:
                selected_item = selected.get(str(row.get("itemid")))
                if selected_item is None or selected_item[1] != probe:
                    continue
                try:
                    clock, value = int(row["clock"]), float(row["value"])
                except (KeyError, ValueError, TypeError, OverflowError):
                    continue
                if not int(start.timestamp()) <= clock <= int(end.timestamp()) or not isfinite(value):
                    continue
                channel = channels[selected_item[0]]
                timestamp = datetime.fromtimestamp(clock, timezone.utc)
                if value_type == 3 and value in (0, 1):
                    channel["successes" if value else "failures"].append(timestamp)
                elif value_type == 0 and 0 < value < 3600:
                    channel["latency"].append({"timestamp": timestamp, "value": value * 1000})
    return {"start": start, "end": end, "channels": channels}


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


def visible_host(row: dict[str, Any], token: str) -> dict[str, Any]:
    health, label = host_health(row)
    return {
        **{key: visible(row.get(key), token) for key in HOST_FIELDS["output"]},
        "interfaces": [{key: visible(item.get(key), token) for key in HOST_FIELDS["selectInterfaces"]}
                       for item in host_interfaces(row)],
        "active_available": visible(str(row.get("active_available", "0")), token),
        "agent_reporting_at": row.get("agent_reporting_at"),
        "health_status": health,
        "health_label": label,
    }


def host_health(row: dict[str, Any]) -> tuple[str, str]:
    if str(row.get("status")) != "0":
        return "unmanaged", "Disabled"
    states = {str(row.get("active_available")), *(str(item.get("available")) for item in host_interfaces(row))}
    if {"1", "2"} <= states:
        return "warning", "Mixed"
    if "1" in states:
        return "up", "Available"
    if "2" in states:
        return "down", "Unavailable"
    if row.get("agent_reporting_at"):
        return "up", "Agent reporting"
    return "unknown", "No availability data"


def source_key(url: str) -> str:
    return sha256(url.encode()).hexdigest()


def hidden_host_ids(db: Session, url: str) -> set[str]:
    return {row[0] for row in db.query(ZabbixHiddenHost.host_id).filter_by(source_key=source_key(url))}


def local_matches(db: Session, url: str, rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    local_by_target: dict[str, set[int]] = {}
    for identifier, ip, hostname in db.query(Device.id, Device.ip, Device.hostname):
        for value in (ip, hostname):
            if target := target_name(value):
                local_by_target.setdefault(target, set()).add(identifier)
    linked = dict(db.query(ZabbixHostImport.host_id, ZabbixHostImport.device_id)
                  .filter_by(source_key=source_key(url)).all())
    result = {}
    for row in rows:
        host_id = str(row.get("hostid"))
        local_ids = {linked[host_id]} if host_id in linked else set()
        for interface in host_interfaces(row):
            if target := interface_target(interface):
                local_ids.update(local_by_target.get(target, ()))
        result[host_id] = sorted(local_ids)
    return result


_identity_cache: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}


def hardware_items(api, rows):
    key = sha256((str(api.url) + api.token + str(sorted(str(row.get("hostid")) for row in rows))).encode()).hexdigest()
    previous = _identity_cache.get(key)
    if previous and monotonic() - previous[0] < 30:
        return previous[1]
    try:
        items = api.rows("item.get", {"hostids": [str(row["hostid"]) for row in rows],
            "output": ["hostid", "key_", "name", "lastvalue", "snmp_oid"],
            "search": {"key_": ["system.hw", "system.descr", "system.objectid", "sysDescr", "sysObjectID", "entPhysical"],
                       "snmp_oid": ["1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.2.0", "1.3.6.1.2.1.47.1.1.1.1."]},
            "searchByAny": True, "limit": 1000}) if rows else []
    except HTTPException:
        return {}  # Inventory remains usable when item-read permission is absent.
    result = {}
    observations = {}
    for item in items:
        fields = result.setdefault(str(item.get("hostid")), {})
        name, value = str(item.get("key_", "")).lower(), clean(item.get("lastvalue"))
        if not value:
            continue
        oid = re.fullmatch(r'(?:get\[)?\.?(\d+(?:\.\d+)+)\]?', str(item.get("snmp_oid", "")).strip())
        if oid:
            observations.setdefault(str(item.get("hostid")), {})[oid[1]] = value
        if (oid and oid[1].startswith(ENTITY)) or "entphysical" in name:
            continue  # Physical rows must be selected by class, never first module model.
        if "model" in name:
            fields.setdefault("model", value)
        elif "vendor" in name or "manufacturer" in name or name.startswith("system.hw.chassis"):
            # system.hw.chassis can contain a complete chassis description, not a vendor.
            if "vendor" in name or "manufacturer" in name:
                fields.setdefault("vendor", value)
        elif "descr" in name:
            fields.setdefault("description", value)
        elif "objectid" in name:
            fields.setdefault("object_id", value)
    for host_id, values in observations.items():
        fields = result.setdefault(host_id, {})
        fields.update({key: value for key, value in snmp_identity(values).items() if value})
    if len(_identity_cache) > 8:
        _identity_cache.clear()
    _identity_cache[key] = (monotonic(), result)
    return result


def inventory_hosts(db: Session, api: ZabbixAPI, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches = local_matches(db, str(api.url), rows)
    readings = hardware_items(api, rows)
    result = []
    for row in rows:
        inventory = row.get("inventory") or {}
        if not isinstance(inventory, dict):
            inventory = {}
        reported = readings.get(str(row.get("hostid")), {})
        found = identity(vendor=clean(inventory.get("vendor")) or reported.get("vendor", ""),
                         model=clean(inventory.get("model")) or clean(inventory.get("hardware")) or reported.get("model", ""),
                         description=reported.get("description", ""), object_id=reported.get("object_id", ""))
        result.append({**visible_host(row, api.token), **{key: visible(value, api.token) if value else None for key, value in found.items()}, "identity_source": "Zabbix inventory / SNMP identity",
                       "local_device_ids": matches[str(row.get("hostid"))]})
    return result


def read_agent_hosts(api: ZabbixAPI, params: dict[str, Any]) -> list[dict[str, Any]]:
    version = tuple(int(part) for part in api.version.split(".")[:2])
    if version >= (6, 2):
        params = {**params, "output": [*HOST_FIELDS["output"], "active_available"]}
    return api.rows("host.get", params)


def annotate_agent_reporting(api: ZabbixAPI, rows: list[dict[str, Any]]) -> None:
    unknown = {str(row["hostid"]): row for row in rows if row.get("hostid") and str(row.get("status")) == "0"
               and str(row.get("active_available", "0")) not in {"1", "2"}
               and not any(str(i.get("available")) in {"1", "2"} for i in host_interfaces(row))}
    if not unknown:
        return
    try:
        items = api.rows("item.get", {"hostids": list(unknown), "output": ["hostid", "lastclock", "delay"],
                                      "filter": {"type": ["0", "7"], "status": "0", "state": "0"}, "limit": 5000})
    except HTTPException:
        return  # Native availability remains useful when item history is unavailable.
    now = int(datetime.now(timezone.utc).timestamp())
    latest: dict[str, int] = {}
    for item in items:
        host_id = str(item.get("hostid"))
        if host_id not in unknown:
            continue
        value = str(item.get("lastclock"))
        if not value.isascii() or not value.isdecimal() or len(value) > 12:
            continue
        stamp = int(value)
        freshness = min(1800, max(300, 2 * (duration(item.get("delay")) or 0)))
        if now - freshness <= stamp <= now + 30:
            latest[host_id] = max(latest.get(host_id, 0), stamp)
    for host_id, stamp in latest.items():
        unknown[host_id]["agent_reporting_at"] = datetime.fromtimestamp(stamp, timezone.utc).isoformat()


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
        rows = read_agent_hosts(api, params)
        annotate_agent_reporting(api, rows[:100])
        return {"hosts": inventory_hosts(db, api, rows[:100]), "limited": len(rows) > 100}


@router.post("/hosts/{host_id}/import")
def import_host(host_id: HostID, user: User = Depends(admin), db: Session = Depends(get_db)):
    connection = db.get(ZabbixConnection, 1)
    if connection is None:
        raise HTTPException(409, "Connect Zabbix in Settings first")
    url, saved_token = connection.api_url, connection.encrypted_token
    with saved_api(db) as api:
        rows = api.rows("host.get", {
            **HOST_FIELDS, "hostids": [host_id], "limit": 1,
            "selectInterfaces": [*HOST_FIELDS["selectInterfaces"], "type", "port", "details"],
            "selectMacros": ["macro", "value", "type"],
        })
        if not rows or str(rows[0].get("hostid")) != host_id:
            raise HTTPException(404, "The selected Zabbix host is unavailable")
        host = rows[0]
        existing = local_matches(db, url, rows)[host_id]
        if existing:
            return {"status": "already_added", "device_id": existing[0], "checks_created": 0, "skipped": {}}
        interfaces = host_interfaces(host)
        resolve_interface_credentials(interfaces, host.get("macros"))
        usable = [item for item in interfaces if interface_target(item)]
        primary_interface = min(usable, key=lambda item: (str(item.get("main")) != "1", str(item.get("type")) != "2"),
                                default=None)
        target = interface_target(primary_interface) if primary_interface else None
        if not target or len(target) > 64:
            raise HTTPException(422, "The Zabbix host needs a usable IP address or DNS interface")
        hostname = target_name(primary_interface.get("dns")) if primary_interface else None
        items = api.rows("item.get", {
            "hostids": [host_id], "output": ["type", "key_", "name", "status", "interfaceid", "delay", "timeout", "snmp_oid"],
            "sortfield": "itemid",
        })
        checks, snmp, skipped = prepare_checks(interfaces, items, target)
        enriched = inventory_hosts(db, api, rows)[0]
        hardware = {field: enriched[field] for field in ("vendor", "model")}
        name = (visible(host.get("name"), api.token) or visible(host.get("host"), api.token) or f"Zabbix host {host_id}")[:128]
        checks = [(visible(check_name, api.token)[:128] or spec.check_type.upper(), spec) for check_name, spec in checks]
    locked = cast(CursorResult, db.execute(update(ZabbixConnection).where(
        ZabbixConnection.id == 1, ZabbixConnection.api_url == url, ZabbixConnection.encrypted_token == saved_token,
    ).values(id=ZabbixConnection.id))).rowcount
    if not locked:
        raise HTTPException(409, "The Zabbix connection changed during import. Try again.")
    existing = local_matches(db, url, [host])[host_id]
    db.query(ZabbixHiddenHost).filter_by(source_key=source_key(url), host_id=host_id).delete()
    if existing:
        db.commit()
        return {"status": "already_added", "device_id": existing[0], "checks_created": 0, "skipped": {}}
    device = Device(name=name, ip=target, hostname=hostname, is_monitored=str(host.get("status")) == "0", is_priority=False, status="unknown", **hardware)
    db.add(device)
    db.flush()
    db.add(ZabbixHostImport(source_key=source_key(url), host_id=host_id, device_id=device.id))
    credentials: dict[str, int] = {}
    for check_name, spec in checks:
        if spec.interface_id and spec.interface_id not in credentials:
            credential = DeviceCredential(device_id=device.id, credential_type="snmp", encrypted_data=snmp[spec.interface_id][1])
            db.add(credential)
            db.flush()
            credentials[spec.interface_id] = credential.id
        params = dict(spec.params)
        if spec.interface_id:
            params["stored_credential_id"] = credentials[spec.interface_id]
        check = Check(device_id=device.id, name=check_name, check_type=spec.check_type, target=spec.target,
                      params=params, interval_seconds=spec.interval, timeout_seconds=spec.timeout,
                      retries=0, is_enabled=spec.enabled)
        check.state = CheckState(current_status="unknown", message="Imported, awaiting first poll")
        db.add(check)
    db.add(AuditLog(user_id=user.id, action="zabbix.host_imported", entity_type="device",
                    entity_id=str(device.id), details={"zabbix_host_id": host_id, "checks": len(checks)}))
    db.commit()
    return {"status": "added", "device_id": device.id, "checks_created": len(checks), "skipped": dict(skipped)}


@router.get("/problems")
def read_problems(db: Session = Depends(get_db)):
    with saved_api(db) as api:
        params: dict[str, Any] = {"output": PROBLEM_FIELDS, "recent": False, "limit": 100,
                                  "sortfield": "eventid", "sortorder": "DESC"}
        hidden = hidden_host_ids(db, str(api.url))
        inventory = api.rows("host.get", HOST_FIELDS)
        matches = local_matches(db, str(api.url), inventory)
        priority = {identifier for identifier, in db.query(Device.id).filter(Device.is_priority.is_(True))}
        host_ids = [host_id for host_id, identifiers in matches.items() if host_id not in hidden and priority.intersection(identifiers)]
        if not host_ids:
            return []
        params["hostids"] = host_ids
        rows = api.rows("problem.get", params)
        return [{key: visible(row.get(key), api.token) for key in PROBLEM_FIELDS} for row in rows[:100]]


@router.get("/inventory")
def read_inventory(db: Session = Depends(get_db)):
    if db.get(ZabbixConnection, 1) is None:
        return {"configured": False, "hosts": []}
    with saved_api(db) as api:
        rows = read_agent_hosts(api, {**HOST_FIELDS, "sortfield": "host"})
        hidden = hidden_host_ids(db, str(api.url))
        rows = [row for row in rows if str(row.get("hostid")) not in hidden]
        annotate_agent_reporting(api, rows)
        return {"configured": True, "hosts": inventory_hosts(db, api, rows)}


@fleet_router.get("/fleet")
def fleet_overview(db: Session = Depends(get_db), priority_only: bool = False):
    query = db.query(Device.id, Device.name, Device.ip, Device.site_id, Device.status)
    if priority_only:
        query = query.filter(Device.is_priority.is_(True))
    local = {row.id: row for row in query}
    zabbix_unavailable = False
    try:
        hosts = read_inventory(db)["hosts"]
    except HTTPException:
        hosts = []
        zabbix_unavailable = True
    if priority_only:
        hosts = [host for host in hosts if any(identifier in local for identifier in host["local_device_ids"])]
    covered = {identifier for host in hosts for identifier in host["local_device_ids"]}
    devices: list[dict[str, Any]] = [{"id": f"local-{item.id}", "name": item.name, "ip": item.ip, "site_id": item.site_id,
                "status": item.status, "source": "zaddesh"} for item in local.values() if item.id not in covered]
    for host in hosts:
        interfaces = sorted(host["interfaces"], key=lambda interface: interface["main"] != "1")
        address = next((item["ip"] if item["useip"] == "1" else item["dns"] for item in interfaces
                        if (item["ip"] if item["useip"] == "1" else item["dns"])), "")
        site_ids = {local[identifier].site_id for identifier in host["local_device_ids"] if identifier in local}
        devices.append({"id": f"zabbix-{host['hostid']}", "name": host["name"] or host["host"], "ip": address,
                        "site_id": next(iter(site_ids)) if len(site_ids) == 1 else None,
                        "status": host["health_status"], "status_label": host["health_label"], "source": "zabbix"})
    stats = {"total_devices": len(devices), "up": 0, "down": 0, "warning": 0, "unknown": 0, "unmanaged": 0}
    for item in devices:
        status = item["status"]
        if status not in {"up", "down", "warning", "unknown", "unmanaged"}:
            item["status"] = status = "unknown"
        stats[status] += 1
    return {"stats": stats, "devices": sorted(devices, key=lambda item: (item["name"].casefold(), item["id"])),
            "zabbix_unavailable": zabbix_unavailable}


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
