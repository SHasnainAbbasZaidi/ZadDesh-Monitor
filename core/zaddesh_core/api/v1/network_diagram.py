from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from datetime import datetime, timedelta
from secrets import compare_digest, token_hex
from typing import Annotated, Literal, cast

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, or_, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import get_current_user, require_role, verify_collector_token
from zaddesh_core.api.v1.incidents import iso
from zaddesh_core.api.v1.stream import notify_change
from zaddesh_core.config import settings
from zaddesh_core.database import SessionLocal, get_db
from zaddesh_core.incidents import redact, utc_now
from zaddesh_core.models.alerts import Alert
from zaddesh_core.models.auth import AuditLog, User
from zaddesh_core.models.checks import Check
from zaddesh_core.models.infrastructure import Collector, Device, DeviceCredential
from zaddesh_core.models.system import Dashboard, Widget
from zaddesh_core.models.topology import TopologyChange, TopologyDiagram, TopologyLink, TopologyNode, TopologyRun
from zaddesh_core.security import allows_secret_transport, decrypt_secret
from zaddesh_core.topology import MAC_ID, identifiable_neighbor, link_key, merge_observation

logger = logging.getLogger(__name__)
AUTOMATIC_DIAGRAM = "inventory"
DISCOVERY_QUEUE_LIMIT = 64
DISCOVERY_REFRESH = timedelta(minutes=5)


def enabled() -> None:
    if not settings.features.network_diagram_beta:
        raise HTTPException(404, "Network Diagram Beta is disabled")


admin = require_role(["admin"])
router = APIRouter(prefix="/network-diagram", dependencies=[Depends(admin), Depends(enabled)], tags=["Network Diagram"])
worker = APIRouter(prefix="/collectors", dependencies=[Depends(enabled)], tags=["Topology collector"])
widgets = APIRouter(prefix="/network-widgets", dependencies=[Depends(get_current_user), Depends(enabled)], tags=["Network Diagram widget"])


class DiagramCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class DiagramUpdate(BaseModel):
    published: bool | None = None
    layout: Literal["hierarchical", "force", "site", "subnet", "expanded"] | None = None
    positions: dict[int, tuple[float, float]] | None = Field(default=None, max_length=2000)


def audit(db, user, action, entity, details):
    db.add(AuditLog(user_id=user.id if user else None, action=action, entity_type="network_diagram",
                    entity_id=str(entity), details=details))


def diagram(db, identifier, published=False):
    item = db.get(TopologyDiagram, identifier)
    if item is None or (published and not item.published):
        raise HTTPException(404, "Diagram is unavailable")
    return item


@router.get("")
def diagrams(db: Session = Depends(get_db)):
    return [{"id": item.id, "name": item.name, "published": item.published, "automatic": item.automatic_key is not None}
            for item in db.query(TopologyDiagram).order_by(TopologyDiagram.automatic_key.is_(None),
                                                           TopologyDiagram.name).limit(200)]


@router.post("", status_code=201)
def create_diagram(payload: DiagramCreate, user: User = Depends(admin), db: Session = Depends(get_db)):
    item = TopologyDiagram(name=payload.name.strip(), layout={"mode": "hierarchical"})
    if not item.name:
        raise HTTPException(422, "Enter a diagram name")
    db.add(item)
    db.flush()
    audit(db, user, "topology.created", item.id, {})
    db.commit()
    return {"id": item.id, "name": item.name}


def graph(db: Session, item: TopologyDiagram):
    alerts = dict(db.query(Alert.device_id, func.count(Alert.id)).filter(
        Alert.status.in_(("active", "acknowledged")),
    ).group_by(Alert.device_id).all())
    rows = db.query(TopologyNode, Device).outerjoin(Device, TopologyNode.device_id == Device.id).filter(
        TopologyNode.diagram_id == item.id,
    ).order_by(TopologyNode.id).all()
    rows = [(node, device) for node, device in rows if device or node.manual or identifiable_neighbor(node.label)]
    node_ids = {node.id for node, _ in rows}
    aliases: dict[str, list[int]] = {}
    for node, device in rows:
        alias = str(node.metadata_json.get("sys_name") or "").strip().casefold()
        if device and alias:
            aliases.setdefault(alias, []).append(node.id)
    canonical = {}
    for node, device in rows:
        matches = aliases.get(node.label.strip().casefold(), [])
        if not device and not node.manual and len(matches) == 1:
            canonical[node.id] = matches[0]
    rows = [(node, device) for node, device in rows if node.id not in canonical]
    link_rows = db.query(TopologyLink).filter_by(diagram_id=item.id).order_by(TopologyLink.manual.desc(), TopologyLink.id)
    links = {}
    for link in link_rows:
        source = canonical.get(link.source_id, link.source_id)
        target = canonical.get(link.target_id, link.target_id)
        if source == target or source not in node_ids or target not in node_ids:
            continue
        key = link_key(source, link.source_port, target, link.target_port)
        if key in links:
            previous = links[key]
            if (not previous[0].manual and not previous[0].evidence.get("configured_vlans")
                    and (link.evidence.get("configured_vlans") or
                         (link.source_id == source and link.target_id == target))):
                links[key] = link, source, target
        else:
            links[key] = link, source, target
    return {"id": item.id, "name": item.name, "published": item.published, "layout": item.layout,
            "automatic": item.automatic_key is not None,
            "nodes": [{"id": node.id, "label": node.label, "device_id": device.id if device else None,
                       "site_id": device.site_id if device else None, "ip": device.ip if device else None,
                       "status": device.status if device else "unmanaged", "alerts": alerts.get(device.id, 0) if device else 0,
                       "kind": node.kind, "manual": node.manual, "interfaces": node.metadata_json.get("interfaces", {})}
                      for node, device in rows],
            "links": [{"id": link.id, "source": source, "target": target,
                       "source_port": "" if MAC_ID.fullmatch(link.source_port.strip()) else link.source_port,
                       "target_port": "" if MAC_ID.fullmatch(link.target_port.strip()) else link.target_port, "method": link.method,
                       "confidence": "high" if link.method in {"lldp", "cdp"} else "manual",
                       "status": link.status, "speed": link.speed, "utilization": link.utilization, "manual": link.manual,
                       "configured_vlans": link.evidence.get("configured_vlans", [])}
                      for link, source, target in links.values()]}


@router.get("/{diagram_id}")
def read_diagram(diagram_id: int, db: Session = Depends(get_db)):
    return graph(db, diagram(db, diagram_id))


@router.patch("/{diagram_id}")
def update_diagram(diagram_id: int, payload: DiagramUpdate, user: User = Depends(admin), db: Session = Depends(get_db)):
    item = diagram(db, diagram_id)
    if payload.published is not None:
        item.published = payload.published
    if payload.positions is not None and any(not all(-100000 <= coord <= 100000 for coord in point) for point in payload.positions.values()):
        raise HTTPException(422, "Layout position is outside the supported range")
    item.layout = {
        "mode": payload.layout or item.layout.get("mode", "hierarchical"),
        "positions": {str(key): point for key, point in payload.positions.items()} if payload.positions is not None
        else item.layout.get("positions", {}),
    }
    audit(db, user, "topology.updated", diagram_id, {"published": item.published, "layout": item.layout["mode"]})
    db.commit()
    notify_change()
    return graph(db, item)


def allowed(address: str) -> bool:
    try:
        ranges = [ipaddress.ip_network(item, strict=True) for item in settings.features.topology_allowed_ranges]
    except ValueError:
        raise HTTPException(503, "Topology CIDR configuration is invalid") from None
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not ip.is_unspecified and not ip.is_multicast and any(ip in network for network in ranges)


def discoverable(check: Check, device: Device) -> bool:
    params = check.params or {}
    return (check.is_enabled and device.is_monitored and check.check_type == "snmp"
            and check.target == device.ip and allowed(device.ip)
            and params.get("version", "3") == "3"
            and params.get("security_level", "authPriv") in {"authPriv", "authNoPriv"}
            and isinstance(params.get("stored_credential_id"), int))


def lock_automatic_diagram(db: Session, item: TopologyDiagram) -> None:
    # A write lock serializes queue capacity and deduplication on SQLite and PostgreSQL.
    db.execute(update(TopologyDiagram).where(TopologyDiagram.id == item.id).values(
        automatic_key=TopologyDiagram.automatic_key,
    ))


def schedule_inventory(db: Session, now: datetime) -> int:
    checks_by_device: dict[int, int] = {}
    for check, device in db.query(Check, Device).join(Device, Check.device_id == Device.id).filter(
        Check.check_type == "snmp", Check.is_enabled.is_(True), Device.is_monitored.is_(True),
    ).order_by(Check.id.desc()):
        if device.id not in checks_by_device and discoverable(check, device):
            checks_by_device[device.id] = check.id
    if not checks_by_device:
        return 0

    item = db.query(TopologyDiagram).filter_by(automatic_key=AUTOMATIC_DIAGRAM).first()
    if item is None:
        item = TopologyDiagram(name="Automatic network map", automatic_key=AUTOMATIC_DIAGRAM,
                               layout={"mode": "hierarchical"})
        db.add(item)
        db.flush()
        audit(db, None, "topology.created", item.id, {"automatic": True})
    else:
        lock_automatic_diagram(db, item)

    runs = db.query(TopologyRun).filter_by(diagram_id=item.id)
    pending = [identifier for identifier, in runs.with_entities(TopologyRun.check_id).filter(
        TopologyRun.status.in_(("queued", "running", "processing")),
    )]
    latest = dict(runs.with_entities(TopologyRun.check_id, func.max(TopologyRun.created_at))
                  .group_by(TopologyRun.check_id).all())
    pending_checks = set(pending)
    cutoff = now - DISCOVERY_REFRESH
    due = [identifier for identifier in checks_by_device.values()
           if identifier not in pending_checks and (identifier not in latest or latest[identifier] <= cutoff)]
    due.sort(key=lambda identifier: (identifier in latest, latest.get(identifier, now), identifier))
    due = due[:max(0, DISCOVERY_QUEUE_LIMIT - len(pending))]
    if due:
        runs.filter(TopologyRun.status.in_(("complete", "failed")),
                    TopologyRun.created_at < now - timedelta(days=1)).delete(synchronize_session=False)
    db.add_all(TopologyRun(diagram_id=item.id, check_id=identifier, status="queued", created_at=now) for identifier in due)
    db.commit()
    return len(due)


def schedule_inventory_once() -> None:
    with SessionLocal() as db:
        if schedule_inventory(db, utc_now()):
            notify_change()


async def topology_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(schedule_inventory_once)
        except (SQLAlchemyError, HTTPException) as error:
            logger.warning("Automatic topology scheduling will retry (%s)", type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), 15)
        except asyncio.TimeoutError:
            pass


class Discovery(BaseModel):
    checks: list[int] = Field(min_length=1, max_length=64)


@router.post("/{diagram_id}/discovery", status_code=202)
def queue_discovery(diagram_id: int, payload: Discovery, user: User = Depends(admin), db: Session = Depends(get_db)):
    item = diagram(db, diagram_id)
    identifiers = set(payload.checks)
    rows = db.query(Check, Device).join(Device, Check.device_id == Device.id).filter(Check.id.in_(identifiers)).all()
    if len(rows) != len(identifiers) or any(not discoverable(check, device) for check, device in rows):
        raise HTTPException(422, "Each seed must use an enabled stored SNMPv3 credential and a permitted device IP")
    if item.automatic_key:
        lock_automatic_diagram(db, item)
    pending = [identifier for identifier, in db.query(TopologyRun.check_id).filter(
        TopologyRun.diagram_id == diagram_id, TopologyRun.status.in_(("queued", "running", "processing")),
    )]
    identifiers.difference_update(pending)
    if len(pending) + len(identifiers) > DISCOVERY_QUEUE_LIMIT:
        raise HTTPException(409, "This diagram already has too many queued or running seeds")
    runs = [TopologyRun(diagram_id=diagram_id, check_id=identifier, status="queued", created_at=utc_now()) for identifier in identifiers]
    db.add_all(runs)
    audit(db, user, "topology.discovery_queued", diagram_id, {"checks": sorted(identifiers)})
    db.commit()
    notify_change()
    return {"queued": len(runs)}


@router.get("/{diagram_id}/discovery")
def discovery_runs(diagram_id: int, db: Session = Depends(get_db)):
    diagram(db, diagram_id)
    return [{"id": run.id, "status": run.status, "message": run.message, "device": device.name,
             "created_at": iso(run.created_at), "finished_at": iso(run.finished_at)}
            for run, device in db.query(TopologyRun, Device).join(Check, TopologyRun.check_id == Check.id)
            .join(Device, Check.device_id == Device.id).filter(TopologyRun.diagram_id == diagram_id)
            .order_by(TopologyRun.id.desc()).limit(100)]


@worker.post("/topology-next")
def next_job(request: Request, collector: Collector | None = Depends(verify_collector_token), db: Session = Depends(get_db)):
    if collector is None or not allows_secret_transport(request):
        raise HTTPException(403, "Individual collector authentication over HTTPS or loopback is required")
    now = utc_now()
    pending = or_(TopologyRun.status == "queued", (TopologyRun.status == "running") & (
        TopologyRun.started_at < now - timedelta(seconds=90)))
    query = db.query(TopologyRun, Check, Device).join(Check, TopologyRun.check_id == Check.id).join(
        Device, Check.device_id == Device.id,
    ).filter(pending)
    if collector.site_id is not None:
        query = query.filter(Device.site_id == collector.site_id)
    row = query.order_by(TopologyRun.id).first()
    if row is None:
        return Response(status_code=204)
    run, check, device = row
    lease = token_hex(24)
    claimed = cast(CursorResult, db.execute(update(TopologyRun).where(TopologyRun.id == run.id, pending).values(
        status="running", started_at=now, collector_id=collector.id, lease=lease,
    ))).rowcount
    if not claimed:
        return Response(status_code=204)
    params = dict(check.params)
    credential_id = params.pop("stored_credential_id", None)
    credential = db.query(DeviceCredential).filter_by(id=credential_id, device_id=device.id, credential_type="snmp").first()
    try:
        if credential is None or not discoverable(check, device):
            raise ValueError
        secrets = json.loads(decrypt_secret(credential.encrypted_data))
    except (InvalidTag, RuntimeError, ValueError):
        run.status, run.finished_at, run.message = "failed", now, "Seed or stored credential is no longer available"
        db.commit()
        notify_change()
        return Response(status_code=204)
    task = {"check_id": check.id, "device_id": device.id, "device_name": device.name, "name": check.name,
            "check_type": "snmp", "target": device.ip, "params": params, "snmp_credentials": secrets,
            "interval_seconds": check.interval_seconds, "timeout_seconds": check.timeout_seconds, "retries": check.retries}
    db.commit()
    notify_change()
    return {"run_id": run.id, "lease": lease, "task": task}


class JobResult(BaseModel):
    lease: str = Field(min_length=48, max_length=48)
    values: dict[Annotated[str, StringConstraints(pattern=r"^\d+(\.\d+){1,63}$", max_length=255)],
                 Annotated[str, StringConstraints(max_length=512)]] = Field(default_factory=dict, max_length=1600)
    error: Literal["SNMPv3 session timed out", "SNMPv3 authentication failed or system MIB did not respond",
                   "SNMPv3 agent returned no readable system data", "SNMPv3 discovery timed out",
                   "SNMP engine discovery failed", "Could not open SNMP session",
                   "SNMP credential is missing or invalid", "SNMP authentication password is too short",
                   "SNMP privacy password is too short", "Unsupported SNMP authentication protocol",
                   "Unsupported SNMP privacy protocol", "Discovery requires authenticated SNMPv3"] | None = None


@worker.post("/topology-result/{run_id}")
def submit_result(run_id: int, payload: JobResult, request: Request, collector: Collector | None = Depends(verify_collector_token),
                  db: Session = Depends(get_db)):
    if collector is None or not allows_secret_transport(request):
        raise HTTPException(403, "Individual collector authentication over HTTPS or loopback is required")
    run = db.query(TopologyRun).filter_by(id=run_id, collector_id=collector.id).with_for_update().first()
    if run is None or not run.lease or not compare_digest(run.lease, payload.lease):
        raise HTTPException(404, "Discovery lease not found")
    if run.status in {"complete", "failed"}:
        return {"status": run.status}
    check = db.get(Check, run.check_id)
    device = db.get(Device, check.device_id) if check else None
    if not check or not device or not discoverable(check, device) or (collector.site_id is not None and collector.site_id != device.site_id):
        raise HTTPException(403, "The seed is no longer available to this collector")
    claimed = cast(CursorResult, db.execute(update(TopologyRun).where(TopologyRun.id == run.id, TopologyRun.status == "running",
                                                    TopologyRun.lease == payload.lease).values(status="processing"))).rowcount
    if not claimed:
        raise HTTPException(409, "Discovery lease is no longer current")
    if payload.error or not payload.values:
        run.status, run.message = "failed", payload.error or "SNMPv3 discovery returned no data"
    else:
        db.query(TopologyDiagram).filter_by(id=run.diagram_id).with_for_update().one()
        count = merge_observation(db, run.diagram_id, device, payload.values)
        run.status, run.message = "complete", f"Collected {len(payload.values)} OIDs and observed {count} links"
    run.finished_at = utc_now()
    audit(db, None, "topology.discovery_finished", run.diagram_id, {"run_id": run.id, "status": run.status})
    db.commit()
    notify_change()
    return {"status": run.status}


@router.get("/{diagram_id}/changes")
def changes(diagram_id: int, db: Session = Depends(get_db)):
    diagram(db, diagram_id)
    return [{"id": row.id, "kind": row.kind, "description": row.description, "decision": row.decision}
            for row in db.query(TopologyChange).filter_by(diagram_id=diagram_id).order_by(TopologyChange.id.desc()).limit(100)]


class Decision(BaseModel):
    decision: Literal["approved", "ignored"]


@router.patch("/{diagram_id}/changes/{change_id}")
def decide_change(diagram_id: int, change_id: int, payload: Decision, user: User = Depends(admin), db: Session = Depends(get_db)):
    row = db.query(TopologyChange).filter_by(diagram_id=diagram_id, id=change_id).first()
    if row is None:
        raise HTTPException(404, "Change not found")
    row.decision = payload.decision
    audit(db, user, "topology.change_reviewed", diagram_id, {"change_id": change_id, "decision": row.decision})
    db.commit()
    notify_change()
    return {"decision": row.decision}


class ManualNode(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    kind: Literal["router", "switch", "firewall", "ap", "server", "ups", "printer", "unmanaged", "internet"] = "unmanaged"


@router.put("/{diagram_id}/nodes/{node_id}", status_code=204)
def edit_node(diagram_id: int, node_id: int, payload: ManualNode, user: User = Depends(admin), db: Session = Depends(get_db)):
    diagram(db, diagram_id)
    item = db.query(TopologyNode).filter_by(diagram_id=diagram_id, id=node_id).first() if node_id else None
    if item is None and node_id:
        raise HTTPException(404, "Node not found")
    if item is None:
        item = TopologyNode(diagram_id=diagram_id, key="manual:" + token_hex(16))
        db.add(item)
    item.label, item.kind, item.manual = redact(payload.label), payload.kind, True
    audit(db, user, "topology.node_edited", diagram_id, {"node_id": node_id})
    db.commit()
    notify_change()


class ManualLink(BaseModel):
    source: int
    target: int
    source_port: str = Field("", max_length=255)
    target_port: str = Field("", max_length=255)
    configured_vlans: list[Annotated[int, Field(ge=1, le=4094)]] | None = Field(None, max_length=4094)


@router.put("/{diagram_id}/links/{link_id}", status_code=204)
def edit_link(diagram_id: int, link_id: int, payload: ManualLink, user: User = Depends(admin), db: Session = Depends(get_db)):
    diagram(db, diagram_id)
    if payload.source == payload.target or db.query(TopologyNode).filter(TopologyNode.diagram_id == diagram_id,
        TopologyNode.id.in_([payload.source, payload.target])).count() != 2:
        raise HTTPException(422, "Choose two different nodes from this diagram")
    item = db.query(TopologyLink).filter_by(diagram_id=diagram_id, id=link_id).first() if link_id else None
    if item is None and link_id:
        raise HTTPException(404, "Link not found")
    if item is not None and payload.configured_vlans is not None:
        item.evidence = {**item.evidence, "configured_vlans": sorted(set(payload.configured_vlans))}
        audit(db, user, "topology.link_vlans_recorded", diagram_id, {"link_id": link_id})
        db.commit()
        notify_change()
        return
    key = link_key(payload.source, payload.source_port, payload.target, payload.target_port)
    duplicate = db.query(TopologyLink).filter_by(diagram_id=diagram_id, key=key).first()
    if duplicate and (item is None or duplicate.id != item.id):
        raise HTTPException(409, "A link already connects those ports")
    if item is None:
        item = TopologyLink(diagram_id=diagram_id)
        db.add(item)
    item.key, item.source_id, item.target_id = key, payload.source, payload.target
    item.source_port, item.target_port = redact(payload.source_port), redact(payload.target_port)
    item.method, item.manual = "manual", True
    if payload.configured_vlans is not None:
        item.evidence = {**(item.evidence or {}), "configured_vlans": sorted(set(payload.configured_vlans))}
    audit(db, user, "topology.link_edited", diagram_id, {"link_id": link_id})
    db.commit()
    notify_change()


@router.delete("/{diagram_id}/{kind}/{entity_id}", status_code=204)
def remove_entity(diagram_id: int, kind: Literal["nodes", "links"], entity_id: int,
                  user: User = Depends(admin), db: Session = Depends(get_db)):
    model = TopologyNode if kind == "nodes" else TopologyLink
    if not db.query(model).filter_by(diagram_id=diagram_id, id=entity_id).delete():
        raise HTTPException(404, "Diagram element not found")
    audit(db, user, "topology.element_removed", diagram_id, {"kind": kind, "id": entity_id})
    db.commit()
    notify_change()


@widgets.get("/published")
def published_diagrams(db: Session = Depends(get_db)):
    return [{"id": item.id, "name": item.name} for item in db.query(TopologyDiagram).filter_by(published=True).order_by(TopologyDiagram.name)]


class WidgetSettings(BaseModel):
    diagram_id: int | None = None
    site_id: int | None = None
    problems_only: bool = False
    labels: bool = True
    height: int = Field(340, ge=200, le=900)


def user_widget(db, user_id):
    return db.query(Widget).join(Dashboard).filter(Dashboard.user_id == user_id,
                                                    Widget.widget_type == "network_diagram_live").first()


@widgets.get("/mine")
def read_widget(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = user_widget(db, user.id)
    if item is None:
        return {"settings": None, "diagram": None}
    config = WidgetSettings.model_validate(item.config)
    selected = db.get(TopologyDiagram, config.diagram_id) if config.diagram_id else None
    if not selected or not selected.published:
        return {"settings": config, "diagram": None}
    payload = graph(db, selected)
    payload["nodes"] = [
        {key: value for key, value in node.items() if key not in {"interfaces", "ip", "device_id"}}
        for node in payload["nodes"]
        if (config.site_id is None or node["site_id"] == config.site_id)
        and (not config.problems_only or node["alerts"] or node["status"] in {"warning", "down"})
    ]
    ids = {node["id"] for node in payload["nodes"]}
    payload["links"] = [link for link in payload["links"] if link["source"] in ids and link["target"] in ids]
    return {"settings": config, "diagram": payload}


@widgets.put("/mine", status_code=204)
def save_widget(payload: WidgetSettings, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = user_widget(db, user.id)
    if payload.diagram_id is None:
        if item:
            db.delete(item)
    else:
        diagram(db, payload.diagram_id, published=True)
        if item is None:
            dashboard = db.query(Dashboard).filter_by(user_id=user.id, title="Overview").first()
            if dashboard is None:
                dashboard = Dashboard(user_id=user.id, title="Overview")
                db.add(dashboard)
                db.flush()
            item = Widget(dashboard_id=dashboard.id, widget_type="network_diagram_live", title="Network Diagram (Live)")
            db.add(item)
        item.config = payload.model_dump()
    db.commit()
