"""LAN-only endpoint correlation and existing dashboard/widget persistence."""
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import get_current_user, require_role
from zaddesh_core.database import get_db
from zaddesh_core.endpoint_tracing import latest_nodes, mac_address
from zaddesh_core.incidents import utc_now
from zaddesh_core.infrastructure_monitoring import classify
from zaddesh_core.models import Device, Event
from zaddesh_core.models.auth import User
from zaddesh_core.models.system import Dashboard, Widget
from zaddesh_core.models.topology import TopologyNode, TopologyDiagram, TopologyLink
from zaddesh_core.topology import port_key
from zaddesh_core.models import Check, CheckState, MetricRaw
from pydantic import HttpUrl, IPvAnyAddress

router = APIRouter(prefix='/dashboard-tools', tags=['Dashboard tools'])
admin = require_role(['admin'])


def interface_inventory(db):
    nodes = latest_nodes(db.query(TopologyNode).filter(TopologyNode.device_id.is_not(None)).all())
    result = []
    for device in db.query(Device).filter(Device.is_monitored.is_(True)):
        node = nodes.get(device.id)
        metadata = node.metadata_json if node else {}
        category = classify({'name': f'{device.name} {device.vendor or ""} {device.os or ""} {metadata.get("sys_description", "")} '})
        ports = []
        for index, port in metadata.get('interfaces', {}).items():
            at = port.get('at')
            try:
                fresh = at is not None and utc_now()-datetime.fromisoformat(at.removesuffix('Z')) <= timedelta(minutes=15)
            except (ValueError, TypeError):
                fresh = False
            ports.append({'index': index, **port, 'fresh': fresh,
                          'status': port.get('status', 'unknown') if fresh else 'unknown'})
        result.append({'id': device.id, 'name': device.name, 'ip': device.ip, 'category': category,
                       'status': device.status, 'observed_at': metadata.get('observed_at'), 'interfaces': ports})
    return result


@router.get('/interfaces', dependencies=[Depends(admin)])
def interfaces(device_id: int | None = None, db: Session = Depends(get_db)):
    rows = [row for row in interface_inventory(db) if device_id is None or row['id'] == device_id]
    identifiers = [row['id'] for row in rows]
    test_ids = [check.id for check in db.query(Check).filter(Check.device_id.in_(identifiers), Check.check_type == 'http')
                if (check.params or {}).get('operation') == 'wan_speed_test'] if identifiers else []
    readings = db.query(MetricRaw).filter(MetricRaw.check_id.in_(test_ids), MetricRaw.timestamp >= utc_now()-timedelta(days=30)).order_by(MetricRaw.timestamp.desc()).limit(8192).all() if test_ids else []
    by_device: dict[int, list[Any]] = {}
    for reading in readings:
        by_device.setdefault(reading.device_id, []).append(reading)
    for row in rows:
        results: dict[str, dict[str, Any]] = {}
        for value in by_device.get(row['id'], []):
            index = (value.tags or {}).get('interface_index')
            if index:
                item = results.setdefault(index, {})
                suffix = value.metric_key.split('.')[-1]
                if suffix not in item:
                    item[suffix] = {'value': value.value, 'at': value.timestamp.isoformat()+'Z', 'reason': (value.tags or {}).get('reason')}
        for port in row['interfaces']:
            port['speed_test'] = results.get(port['index'], {})
    return rows


@router.get('/endpoints', dependencies=[Depends(admin)])
def trace_endpoints(q: str = Query('', max_length=128), db: Session = Depends(get_db)):
    devices = {row.id: row for row in db.query(Device).filter(Device.is_monitored.is_(True))}
    nodes = latest_nodes(db.query(TopologyNode).filter(TopologyNode.device_id.is_not(None)).all())
    identities = []
    for event in db.query(Event).filter(Event.timestamp >= utc_now()-timedelta(minutes=15),
        Event.event_type.in_(['authentication', 'login', 'user_login', 'radius_accept', 'firewall_session'])).order_by(Event.timestamp.desc()).limit(500):
        details = event.details or {}
        if details.get('username') and (details.get('mac') or details.get('ip')):
            identities.append({'username': str(details['username'])[:128], 'mac': mac_address(details.get('mac', '')),
                               'ip': details.get('ip'), 'timestamp': event.timestamp.isoformat()+'Z'})
    arp: dict[str, set[str]] = {}
    for node in nodes.values():
        for entry in node.metadata_json.get('endpoints', []):
            arp.setdefault(entry['mac'], set()).update(entry.get('ips', []))
    transit = set()
    by_id = {node.id: node for node in db.query(TopologyNode).filter(TopologyNode.device_id.is_not(None))}
    for link in db.query(TopologyLink):
        for identifier, port in ((link.source_id, link.source_port), (link.target_id, link.target_port)):
            node = by_id.get(identifier)
            if node:
                transit.add((node.device_id, port_key(port)))
    result, seen = [], set()
    for identifier, node in nodes.items():
        device = devices.get(identifier)
        if device is None:
            continue
        for entry in node.metadata_json.get('endpoints', []):
            value = {**entry, 'ips': sorted(arp.get(entry['mac'], set())), 'device_id': identifier,
                     'device': device.name, 'device_ip': device.ip}
            value['association'] = ('ARP adjacency' if entry.get('evidence') == 'IP-MIB ARP' else
                                    'Transit/uplink' if (identifier, port_key(entry['port'])) in transit else 'Candidate access port')
            try:
                value['fresh'] = utc_now()-datetime.fromisoformat(entry['observed_at'].removesuffix('Z')) <= timedelta(minutes=15)
            except (ValueError, KeyError):
                value['fresh'] = False
            identity = next((row for row in identities if row['mac'] == entry['mac'] or (not row['mac'] and row['ip'] in value['ips'])), None)
            if identity:
                value['username'], value['identity_source'] = identity['username'], 'Structured authentication event'
            key = (identifier, entry['mac'], entry.get('fdb_id'), entry['bridge_port'])
            if key in seen:
                continue
            seen.add(key)
            if q and q.casefold() not in ' '.join([value['mac'], *value['ips'], value.get('username') or '', device.name]).casefold() and mac_address(q) != value['mac']:
                continue
            result.append(value)
    return {'entries': result[:2000], 'limited': len(result)>2000, 'refreshed_at': utc_now().isoformat()+'Z',
            'sources': [{'device_id': identifier, 'device': devices[identifier].name,
                         'observed_at': node.metadata_json.get('observed_at'),
                         'entries': len(node.metadata_json.get('endpoints', []))} for identifier, node in nodes.items() if identifier in devices],
            'note': 'Collection is bounded to 512 entries per SNMP table column and the existing global discovery budget; large or restricted tables may be incomplete. FDB entries can also be visible on trunks. This is observed forwarding evidence, not proof of the final access port. Username lookup requires structured authentication events. Missing ARP/FDB data is unavailable, not a disconnected endpoint.'}


class DashboardInput(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    pinned: bool = False


class WidgetInput(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    kind: Literal['metric', 'stat', 'diagram', 'firewall_usage', 'interfaces', 'alerts', 'events']
    device_id: int | None = None
    metric_key: str | None = Field(default=None, max_length=128)
    diagram_id: int | None = None
    interfaces: list[str] = Field(default_factory=list, max_length=256)
    minutes: int = Field(default=60, ge=5, le=525600)
    unit: str = Field(default='', max_length=32)
    width: Literal[1, 2] = 1


def owned(db, identifier, user):
    row = db.get(Dashboard, identifier)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, 'Dashboard not found')
    return row


@router.get('/dashboards')
def dashboards(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{'id': row.id, 'title': row.title, 'pinned': bool(row.layout.get('pinned')) if isinstance(row.layout, dict) else False}
            for row in db.query(Dashboard).filter(Dashboard.user_id == user.id)]


@router.post('/dashboards', status_code=201)
def create_dashboard(payload: DashboardInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    row = Dashboard(title=payload.title, user_id=user.id, layout={'pinned': payload.pinned})
    db.add(row)
    db.commit()
    return {'id': row.id, 'title': row.title, 'pinned': payload.pinned}


@router.put('/dashboards/{identifier}')
def configure_dashboard(identifier: int, payload: DashboardInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    row = owned(db, identifier, user)
    row.title, row.layout = payload.title, {**(row.layout or {}), 'pinned': payload.pinned}
    db.commit()
    return {'id': row.id, 'title': row.title, 'pinned': payload.pinned}


@router.delete('/dashboards/{identifier}')
def delete_dashboard(identifier: int, user: User = Depends(admin), db: Session = Depends(get_db)):
    row = owned(db, identifier, user)
    db.query(Widget).filter(Widget.dashboard_id == identifier).delete()
    db.delete(row)
    db.commit()
    return {'deleted': True}


@router.get('/dashboards/{identifier}/widgets')
def widgets(identifier: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    owned(db, identifier, user)
    return [{'id': row.id, 'title': row.title, **row.config} for row in db.query(Widget).filter(
        Widget.dashboard_id == identifier, Widget.widget_type == 'custom_monitor').order_by(Widget.id)]


@router.put('/dashboards/{identifier}/widgets')
def save_widgets(identifier: int, payload: list[WidgetInput], user: User = Depends(admin), db: Session = Depends(get_db)):
    owned(db, identifier, user)
    if len(payload)>32:
        raise HTTPException(422, 'At most 32 widgets per dashboard')
    for widget in payload:
        if widget.kind in {'metric', 'stat', 'firewall_usage', 'interfaces'}:
            device = db.get(Device, widget.device_id) if widget.device_id else None
            if device is None or not device.is_monitored:
                raise HTTPException(422, 'Select an existing monitored device')
        if widget.kind in {'metric', 'stat'} and not widget.metric_key:
            raise HTTPException(422, 'Select a monitored metric')
        if widget.kind == 'diagram' and (widget.diagram_id is None or db.get(TopologyDiagram, widget.diagram_id) is None):
            raise HTTPException(422, 'Select an existing diagram')
    db.query(Widget).filter(Widget.dashboard_id == identifier, Widget.widget_type == 'custom_monitor').delete()
    for index, widget in enumerate(payload):
        db.add(Widget(dashboard_id=identifier, widget_type='custom_monitor', title=widget.title,
                      config={**widget.model_dump(), 'order': index}, grid_position={'order': index, 'width': widget.width}))
    db.commit()
    return {'saved': len(payload)}


@router.get('/diagrams', dependencies=[Depends(admin)])
def diagram_catalog(db: Session = Depends(get_db)):
    return [{'id': row.id, 'name': row.name} for row in db.query(TopologyDiagram).order_by(TopologyDiagram.name)]


class WanTarget(BaseModel):
    index: str = Field(pattern=r'^\d{1,12}$')
    name: str = Field(min_length=1, max_length=128)
    source_ip: IPvAnyAddress
    download_url: HttpUrl
    upload_url: HttpUrl | None = None


class WanConfig(BaseModel):
    enabled: bool = False
    interval_seconds: int = Field(default=900, ge=300, le=86400)
    max_bytes: int = Field(default=10000000, ge=1000000, le=20000000)
    targets: list[WanTarget] = Field(default_factory=list, max_length=16)


def wan_check(db, device_id):
    return next((row for row in db.query(Check).filter(Check.device_id == device_id, Check.check_type == 'http')
                 if (row.params or {}).get('operation') == 'wan_speed_test'), None)


@router.get('/wan-tests/{device_id}', dependencies=[Depends(admin)])
def wan_settings(device_id: int, db: Session = Depends(get_db)):
    row = wan_check(db, device_id)
    return {'enabled': row.is_enabled, **row.params['configuration']} if row else WanConfig().model_dump()


@router.put('/wan-tests/{device_id}', dependencies=[Depends(admin)])
def save_wan_tests(device_id: int, payload: WanConfig, db: Session = Depends(get_db)):
    device = db.get(Device, device_id)
    if device is None or not device.is_monitored:
        raise HTTPException(404, 'Monitored device not found')
    if payload.enabled and not payload.targets:
        raise HTTPException(422, 'Configure at least one WAN source IP and test URL')
    if len({target.index for target in payload.targets}) != len(payload.targets):
        raise HTTPException(422, 'Each WAN interface can occur only once')
    for target in payload.targets:
        if target.source_ip.is_unspecified or target.source_ip.is_multicast:
            raise HTTPException(422, 'Use a unicast source IP assigned to the collector')
        for url in (target.download_url, target.upload_url):
            if url and (url.username or url.password):
                raise HTTPException(422, 'Test URLs cannot contain credentials')
    inventory = next((row for row in interface_inventory(db) if row['id'] == device_id), None)
    known = {port['index'] for port in inventory['interfaces']} if inventory else set()
    if any(target.index not in known for target in payload.targets):
        raise HTTPException(422, 'Select interfaces actually reported by this device')
    row = wan_check(db, device_id)
    if row is None:
        row = Check(device_id=device_id, name='WAN throughput rotation', check_type='http', target=device.ip,
                    params={}, interval_seconds=payload.interval_seconds, timeout_seconds=25, retries=0)
        db.add(row)
        db.flush()
        db.add(CheckState(check_id=row.id))
    configuration = payload.model_dump(mode='json')
    row.params = {'operation': 'wan_speed_test', 'configuration': configuration}
    row.interval_seconds, row.is_enabled = payload.interval_seconds, payload.enabled
    db.commit()
    return {'saved': True}
