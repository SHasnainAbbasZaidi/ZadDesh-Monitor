from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from zaddesh_core.config import settings
from zaddesh_core.incidents import queue_delivery, redact, suppression_reason, timeline, utc_now
from zaddesh_core.models.alerts import Alert, Event
from zaddesh_core.models.infrastructure import Device
from zaddesh_core.models.incidents import Incident
from zaddesh_core.models.topology import TopologyChange, TopologyLink, TopologyNode

LLDP = "1.0.8802.1.1.2.1.4.1.1"
CDP = "1.3.6.1.4.1.9.9.23.1.2.1.1"
IF = "1.3.6.1.2.1.2.2.1"
IFX = "1.3.6.1.2.1.31.1.1.1"
MAC_ID = re.compile(r"(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}|(?:[0-9a-f]{4}\.){2}[0-9a-f]{4}|[0-9a-f]{12}", re.I)


def identifiable_neighbor(label: str) -> bool:
    value = label.strip()
    return bool(value and value.casefold() not in {"not advertised", "unknown", "n/a", "none", "-"}
                and not MAC_ID.fullmatch(value))


def column(bindings: dict[str, str], oid: str) -> dict[str, str]:
    prefix = oid + "."
    return {key[len(prefix):]: value for key, value in bindings.items() if key.startswith(prefix)}


def number(value: Any) -> int | None:
    return int(value) if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 20 else None


def port_key(port: str) -> str:
    normalized = re.sub(r"\s+", "", port.casefold())
    for long, short in (("tengigabitethernet", "te"), ("gigabitethernet", "gi"),
                        ("fastethernet", "fa"), ("port-channel", "po"), ("ethernet", "eth")):
        if normalized.startswith(long):
            return short + normalized[len(long):]
    return normalized


def link_key(left: int, left_port: str, right: int, right_port: str) -> str:
    return hashlib.sha256(json.dumps(sorted(((left, port_key(left_port)), (right, port_key(right_port))))).encode()).hexdigest()


def change(db: Session, diagram_id: int, kind: str, description: str) -> None:
    db.add(TopologyChange(diagram_id=diagram_id, kind=kind, description=redact(description[:512]), created_at=utc_now()))


def parse_interfaces(data: dict[str, str], prior: dict[str, Any], now: datetime) -> dict[str, Any]:
    names = column(data, IF + ".2") | column(data, IFX + ".1")
    status = column(data, IF + ".8")
    speed = column(data, IF + ".5")
    high_speed = column(data, IFX + ".15")
    incoming = column(data, IFX + ".6")
    outgoing = column(data, IFX + ".10")
    result: dict[str, Any] = {}
    for index, name in names.items():
        rate = (number(high_speed.get(index)) or 0) * 1_000_000 or number(speed.get(index))
        rx = number(incoming.get(index))
        tx = number(outgoing.get(index))
        previous = prior.get(index)
        utilization = None
        if previous and rate and rx is not None and tx is not None and previous.get("rx") is not None and previous.get("tx") is not None:
            seconds = (now - datetime.fromisoformat(previous["at"])).total_seconds()
            if seconds > 0 and rx >= previous["rx"] and tx >= previous["tx"]:
                utilization = min(100.0, 800 * max(rx - previous["rx"], tx - previous["tx"]) / (rate * seconds))
        result[index] = {"name": name, "status": {"1": "up", "2": "down"}.get(status.get(index, ""), "unknown"),
                         "speed": rate, "rx": rx, "tx": tx, "utilization": utilization, "at": now.isoformat()}
    return result


def neighbors(data: dict[str, str]):
    lldp_ports = column(data, "1.0.8802.1.1.2.1.3.7.1.3")
    chassis = column(data, LLDP + ".5")
    names = column(data, LLDP + ".9")
    interface_names = column(data, IF + ".2") | column(data, IFX + ".1")
    addresses: dict[str, list[str]] = {}
    for suffix in column(data, "1.0.8802.1.1.2.1.4.2.1.3"):
        indices = suffix.split(".")
        if len(indices) < 5:
            continue
        family, size = indices[3:5]
        if (family, size) not in {("1", "4"), ("2", "16")} or len(indices[5:]) != int(size):
            continue
        try:
            address = str(ipaddress.ip_address(bytes(map(int, indices[5:]))))
        except ValueError:
            continue
        addresses.setdefault(".".join(indices[:3]), []).append(address)
    lldp_indices = set()
    for suffix, remote_port in column(data, LLDP + ".7").items():
        indices = suffix.split(".")
        management = addresses.get(suffix, [])
        name = names.get(suffix) or ""
        if not identifiable_neighbor(name):
            name = next(iter(management), None) or chassis.get(suffix) or name
        if len(indices) == 3 and name and remote_port:
            index = indices[1]
            lldp_indices.add(index)
            local = lldp_ports.get(index) or ""
            if not identifiable_neighbor(local) or local.isdecimal():
                local = interface_names.get(index) or local or index
            yield "lldp", index, local, name, remote_port, management
    cdp_names = column(data, CDP + ".6")
    for suffix, remote_port in column(data, CDP + ".7").items():
        index = suffix.split(".")[0]
        name = cdp_names.get(suffix)
        local = interface_names.get(index) or index
        if index not in lldp_indices and not any(port_key(lldp_ports.get(i, i)) == port_key(local) for i in lldp_indices) and name and remote_port:
            yield "cdp", index, local, name, remote_port, []


def link_incident(db: Session, link: TopologyLink, device: Device, now: datetime) -> None:
    key = f"link:{link.diagram_id}:{link.id}"
    incident = db.query(Incident).filter_by(correlation_key=key).first()
    alert = db.get(Alert, incident.alert_id) if incident else None
    if link.status == "up" and alert and alert.status != "resolved":
        alert.status, alert.resolved_at = "resolved", now
        assert incident is not None
        if not incident.suppression and not (incident.silenced_until and incident.silenced_until > now):
            queue_delivery(db, alert, "resolved", now)
        incident.suppression = None
        timeline(db, alert.id, now, "resolved", "Interface recovered")
        db.add(Event(device_id=device.id, event_type="link_up", severity="info", title="Link up", message=alert.message, timestamp=now))
    elif link.status == "down":
        title = f"Link Down: {device.name} · {link.source_port}"[:255]
        message = f"Interface {link.source_port} on {device.name} reported down. Remote port: {link.target_port}."
        if alert is None:
            alert = Alert(device_id=device.id, severity="critical", status="active", title=title, message=message, triggered_at=now)
            db.add(alert)
            db.flush()
            incident = Incident(alert_id=alert.id, correlation_key=key, occurrence_count=0, flapping=False, transitions=[])
            db.add(incident)
            timeline(db, alert.id, now, "started", message)
        elif alert.status == "resolved":
            alert.status, alert.triggered_at, alert.resolved_at = "active", now, None
            assert incident is not None
            incident.acknowledged_at = None
            timeline(db, alert.id, now, "reopened", message)
        else:
            return
        assert incident is not None
        incident.suppression = suppression_reason(db, device, now)
        if not incident.suppression and not (incident.silenced_until and incident.silenced_until > now):
            queue_delivery(db, alert, "opened", now)
        incident.occurrence_count += 1
        incident.transitions = [*incident.transitions[-9:], now.isoformat()]
        if len(incident.transitions) >= 5 and (now - datetime.fromisoformat(incident.transitions[-5])).total_seconds() <= 600:
            if not incident.flapping:
                timeline(db, alert.id, now, "flapping", "Five link failures in ten minutes")
            incident.flapping = True
        db.add(Event(device_id=device.id, event_type="link_down", severity="critical", title=title, message=message, timestamp=now))


def merge_observation(db: Session, diagram_id: int, device: Device, data: dict[str, str]) -> int:
    now = utc_now()
    nodes = db.query(TopologyNode).filter_by(diagram_id=diagram_id).all()
    by_key = {item.key: item for item in nodes}
    by_id = {item.id: item for item in nodes}
    known = db.query(Device).all()
    known_names: dict[str, Device | None] = {}
    for item in known:
        for alias in {item.name.casefold(), (item.hostname or "").casefold(), item.ip.casefold()} - {""}:
            known_names[alias] = item if alias not in known_names else None
    inventory = {item.id: item for item in known}
    for item in nodes:
        alias = str(item.metadata_json.get("sys_name") or "").strip().casefold()
        identity = inventory.get(item.device_id)
        if alias and identity and (alias not in known_names or known_names[alias] != identity):
            known_names[alias] = identity if alias not in known_names else None

    def node(label: str, identity: Device | None) -> TopologyNode:
        key = f"device:{identity.id}" if identity else f"neighbor:{label.casefold()}"[:255]
        existing = by_key.get(key)
        if existing:
            return existing
        result = TopologyNode(diagram_id=diagram_id, key=key, label=redact(identity.name if identity else label)[:255],
                              device_id=identity.id if identity else None, kind="unmanaged", metadata_json={})
        db.add(result)
        db.flush()
        change(db, diagram_id, "new_device", f"Discovered {result.label}")
        by_key[key] = result
        by_id[result.id] = result
        return result

    source = node(device.name, device)
    sys_name = data.get("1.3.6.1.2.1.1.5.0", device.name)
    sys_descr = data.get("1.3.6.1.2.1.1.1.0", "")
    interfaces = parse_interfaces(data, source.metadata_json.get("interfaces", {}), now)
    source.metadata_json = redact({"sys_name": sys_name, "sys_description": sys_descr, "interfaces": interfaces})
    if not source.manual:
        description = sys_descr.casefold()
        source.kind = next((kind for kind in ("switch", "router", "firewall", "printer", "ups", "server")
                            if re.search(r"\b" + kind + r"\b", description)), "unmanaged")
    links = {item.key: item for item in db.query(TopologyLink).filter_by(diagram_id=diagram_id)}
    observed = set()

    def update_evidence(link: TopologyLink, port: str, interface: dict, present: bool) -> None:
        previous = link.evidence.get(str(source.id), {})
        if previous.get("present", True) and not present:
            change(db, diagram_id, "missing_link", f"{source.label} {port} no longer advertises this neighbor")
        evidence = {**link.evidence, str(source.id): {
            **{key: interface.get(key) for key in ("status", "speed", "utilization")}, "present": present,
        }}
        old_status = link.status
        readings = [item for item in evidence.values() if isinstance(item, dict)]
        statuses = {item.get("status") for item in readings}
        link.status = "down" if "down" in statuses else "up" if "up" in statuses else "unknown"
        link.speed = min((item["speed"] for item in readings if item.get("speed")), default=None)
        link.utilization = max((item["utilization"] for item in readings if item.get("utilization") is not None), default=None)
        link.evidence = evidence
        if old_status != link.status:
            change(db, diagram_id, "link_status", f"{source.label} {port} is {link.status}")
            if settings.features.incident_workspace:
                anchor = db.get(TopologyNode, link.source_id)
                inventory = db.get(Device, anchor.device_id) if anchor and anchor.device_id else None
                if inventory:
                    link_incident(db, link, inventory, now)

    for method, index, local_port, remote_name, remote_port, management in neighbors(data):
        identity = next((known_names[address] for address in management if known_names.get(address)), None)
        identity = identity or known_names.get(remote_name.strip().casefold())
        if identity is None and not identifiable_neighbor(remote_name):
            continue
        target = node(remote_name, identity)
        if source.id == target.id:
            continue
        key = link_key(source.id, local_port, target.id, remote_port)
        link = links.get(key)
        if link is None:
            link = TopologyLink(diagram_id=diagram_id, key=key, source_id=source.id, target_id=target.id,
                                source_port=redact(local_port), target_port=redact(remote_port), method=method,
                                status="unknown", evidence={})
            db.add(link)
            db.flush()
            links[key] = link
            change(db, diagram_id, "new_link", f"{source.label} {local_port} ↔ {target.label} {remote_port}")
        if link.manual:
            continue
        observed.add(key)
        interface = next((item for item in interfaces.values() if port_key(item["name"]) == port_key(local_port)),
                         interfaces.get(index, {}))
        if method == "lldp":
            link.method = method
        update_evidence(link, local_port, interface, True)
    for key, link in links.items():
        if not link.manual and key not in observed and str(source.id) in link.evidence:
            peer_id = link.target_id if link.source_id == source.id else link.source_id
            peer = by_id.get(peer_id)
            if peer and not (peer.device_id or peer.manual or identifiable_neighbor(peer.label)):
                continue
            port = link.source_port if link.source_id == source.id else link.target_port
            interface = next((item for item in interfaces.values() if port_key(item["name"]) == port_key(port)), None)
            if interface is not None:
                update_evidence(link, port, interface, False)
    return len(observed)
