from datetime import datetime, timedelta, timezone
from secrets import token_hex

import pytest

from zaddesh_core.api.v1.incidents import zabbix_category
from zaddesh_core.config import settings
from zaddesh_core.incidents import redact, utc_now
from zaddesh_core.models.alerts import Alert, Event, MaintenanceWindow
from zaddesh_core.models.checks import Check
from zaddesh_core.models.infrastructure import Device


@pytest.mark.parametrize(("title", "host", "category"), [
    ("High CPU utilization", "Core", "cpu_usage"),
    ("High memory utilization", "Core", "memory_usage"),
    ("Interface Te1/0/1: High bandwidth usage", "Core", "network_usage"),
    ("C: Disk space is critically low", "Core", "storage_shortage"),
    ("Interface Ethernet 2: Link down", "Backup Server", "link_down"),
    ("Interface TenGigabitEthernet1/0/1: Link down", "Core Switch", "link_down"),
    ("Interface Gi0/35(-->Data_Ports<--): Link down", "Core Switch", "other"),
    ("Ethernet has changed to lower speed than it was before", "Core Switch", "other"),
])
def test_zabbix_critical_classification(title, host, category):
    assert zabbix_category(title, host) == category


def test_incident_lifecycle_including_buffered_flaps(client, admin_headers, viewer_headers, monkeypatch):
    assert client.get("/api/v1/incidents/alerts", headers=admin_headers).status_code == 404
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    token = token_hex(32)
    monkeypatch.setattr(settings.collector, "shared_secret", token)
    created = client.post("/api/v1/devices", headers=admin_headers, json={
        "name": "Incident test", "ip": "192.0.2.4", "initial_check_types": ["http"], "is_priority": True,
    })
    device = created.json()["id"]
    check = client.get(f"/api/v1/checks?device_id={device}", headers=admin_headers).json()[0]["id"]
    start = datetime.now(timezone.utc) - timedelta(minutes=2)

    def sample(indices):
        points = [{"device_id": device, "check_id": check, "metric_key": metric, "value": value,
                   "timestamp": (start + timedelta(seconds=index)).isoformat()}
                  for index in indices for metric, value in (
                      ("http.available", index % 2), ("http.response_time_ms", 8.5),
                  )]
        assert client.post("/api/v1/metrics/ingest", headers={"Authorization": f"Bearer {token}"},
                           json={"points": points}).status_code == 202

    route = f"/api/v1/incidents/alerts?device_id={device}"
    sample([0])
    alert = client.get(route, headers=viewer_headers).json()["alerts"][0]
    assert alert["occurrences"] == 1 and alert["status"] == "active"
    action = {"ids": [alert["id"]], "action": "acknowledge", "comment": "Checking the port"}
    assert client.post("/api/v1/incidents/actions", headers=viewer_headers, json=action).status_code == 403
    assert client.post("/api/v1/incidents/actions", headers=admin_headers, json=action).status_code == 200
    sample([1])
    detail = f"/api/v1/incidents/alerts/{alert['id']}"
    resolved = client.get(detail, headers=viewer_headers).json()
    assert resolved["status"] == "resolved" and resolved["duration_seconds"] == 1
    assert resolved["samples"] and resolved["events"]
    sample(range(2, 10))
    sample(range(2, 10))
    history = client.get(route, headers=viewer_headers).json()["alerts"]
    assert len(history) == 1 and history[0]["id"] == alert["id"]
    assert history[0]["status"] == "resolved" and history[0]["flapping"] and history[0]["occurrences"] == 5
    kinds = [item["kind"] for item in client.get(detail, headers=viewer_headers).json()["timeline"]]
    assert {"started", "acknowledged", "resolved", "reopened", "flapping"} <= set(kinds)


def test_redacts_structured_and_text_secrets():
    value = redact({"authKey": "sensitive", "message": "password=example community=public Authorization: Bearer example-token"})
    assert value["authKey"] == "[redacted]"
    assert "example" not in value["message"] and "public" not in value["message"]


def test_repeated_probe_failures_keep_counts_and_suppression_transitions(
    client, db_session, admin_headers, monkeypatch,
):
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    token = token_hex(32)
    monkeypatch.setattr(settings.collector, "shared_secret", token)
    device = client.post("/api/v1/devices", headers=admin_headers, json={
        "name": "Repeated failure", "ip": "192.0.2.21", "initial_check_types": ["icmp"], "is_priority": True,
    }).json()["id"]
    check = client.get(f"/api/v1/checks?device_id={device}", headers=admin_headers).json()[0]["id"]
    start = utc_now() - timedelta(minutes=2)
    db_session.add(MaintenanceWindow(name="Maintenance", scope_type="device", scope_id=device,
                                     start_time=start, end_time=start + timedelta(seconds=5)))
    db_session.commit()
    payload = {"points": [
        {"device_id": device, "check_id": check, "metric_key": "icmp.availability", "value": 0,
         "timestamp": (start + timedelta(seconds=index)).isoformat()}
        for index in range(20)
    ]}
    for _ in range(2):
        response = client.post("/api/v1/metrics/ingest", headers={"Authorization": f"Bearer {token}"}, json=payload)
        assert response.status_code == 202
    alert = client.get(f"/api/v1/incidents/alerts?device_id={device}", headers=admin_headers).json()["alerts"][0]
    detail = client.get(f"/api/v1/incidents/alerts/{alert['id']}", headers=admin_headers).json()
    assert detail["occurrences"] == 20
    assert [entry["kind"] for entry in detail["timeline"]] == ["started", "suppressed", "unsuppressed"]


def test_critical_categories_and_alert_cleanup(client, db_session, admin_headers, viewer_headers, monkeypatch):
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    now = utc_now()
    offline = Device(name="Offline device", ip="192.0.2.90", status="down")
    online = Device(name="Online device", ip="192.0.2.91", status="up")
    db_session.add_all([offline, online])
    db_session.flush()
    icmp = Check(device_id=offline.id, name="Offline ping", check_type="icmp", target=offline.ip)
    http = Check(device_id=online.id, name="Web test", check_type="http", target="http://192.0.2.91")
    db_session.add_all([icmp, http])
    db_session.flush()

    def add(title, age, check=None, resolved=False):
        alert = Alert(device_id=check.device_id if check else online.id, check_id=check.id if check else None,
                      severity="critical", status="resolved" if resolved else "active", title=title,
                      triggered_at=now - age, resolved_at=now if resolved else None)
        db_session.add(alert)
        return alert

    down = add("Availability: Offline ping", timedelta(minutes=10), icmp)
    link = add("Link Down: switch · TenGigabitEthernet1/1", timedelta(minutes=2))
    access = add("Link Down: switch · Gi0/1", timedelta(minutes=2))
    server = add("Link Down: Windows Server NIC", timedelta(minutes=2))
    resource_titles = {"cpu_usage": "High CPU utilization", "memory_usage": "High RAM usage",
                       "network_usage": "High bandwidth usage", "storage_shortage": "Disk space is critically low"}
    resources = {category: add(title, timedelta(minutes=2)) for category, title in resource_titles.items()}
    probe = add("Availability: Web test", timedelta(minutes=2), http)
    noise = add("Client disconnected", timedelta(minutes=3))
    old = add("Uplink down", timedelta(hours=8, minutes=1))
    resolved = add("Downlink down", timedelta(minutes=1), resolved=True)
    db_session.add_all([
        Event(event_type="disconnect", title="Client disconnected", severity="critical", timestamp=now),
        Event(event_type="link_down", title="Uplink down", severity="critical", timestamp=now),
    ])
    db_session.commit()
    base = "/api/v1/incidents"

    def ids(view, extra=""):
        response = client.get(f"{base}/alerts?view={view}&hide_connection_noise=true{extra}", headers=viewer_headers)
        assert response.status_code == 200
        return {row["id"] for row in response.json()["alerts"]}

    critical = {down.id, link.id, server.id, *(alert.id for alert in resources.values())}
    assert ids("current", "&category=critical") == critical
    assert ids("current", "&category=device_offline") == {down.id}
    assert ids("current", "&category=link_down") == {link.id, server.id}
    for category, alert in resources.items():
        assert ids("current", f"&category={category}") == {alert.id}
    assert ids("current") == critical | {probe.id, access.id}
    assert ids("history") == {old.id, resolved.id}
    assert noise.id in {row["id"] for row in client.get(base + "/alerts", headers=viewer_headers).json()["alerts"]}
    events = client.get(base + "/events?hide_connection_noise=true&errors_only=true", headers=viewer_headers).json()["events"]
    assert [row["title"] for row in events] == ["Uplink down"]
    count = client.get(base + "/alerts?view=current&category=critical&include_count=true&limit=1", headers=viewer_headers).json()
    assert count["total"] == len(critical)

    def at(delta):
        return (now - delta).replace(tzinfo=timezone.utc).isoformat()

    payload = {"start": at(timedelta(minutes=11)), "end": at(timedelta(minutes=9))}
    assert client.post(base + "/alerts/dismiss", headers=viewer_headers, json=payload).status_code == 403
    assert client.post(base + "/alerts/dismiss", headers=admin_headers,
                       json={"start": payload["end"], "end": payload["start"]}).status_code == 422
    assert client.post(base + "/alerts/dismiss", headers=admin_headers, json=payload).json() == {"removed": 1}
    assert client.post(base + "/alerts/dismiss", headers=admin_headers, json=payload).json() == {"removed": 0}
    assert ids("current", "&category=critical") == critical - {down.id}
    assert client.get(f"{base}/alerts/{down.id}", headers=viewer_headers).status_code == 200
    down.triggered_at = now
    db_session.commit()
    assert ids("current", "&category=critical") == critical

def test_snmp_scalar_failures_are_not_host_offline(client, admin_headers, db_session, monkeypatch):
    from zaddesh_core.models.zabbix import ZabbixHostImport
    monkeypatch.setattr(settings.features, 'incident_workspace', True)
    device = Device(name='Counter target', ip='192.0.2.71', is_priority=True, status='down', collection_mode='scheduled')
    db_session.add(device)
    db_session.flush()
    check = Check(name='Interface counter', device_id=device.id, check_type='snmp', target=device.ip, params={'oid': '1.3.6.1.2.1.2.2.1.10.1'})
    db_session.add(check)
    db_session.flush()
    db_session.add(Alert(device_id=device.id, check_id=check.id, title='Availability: Interface counter', severity='critical'))
    db_session.commit()
    path='/api/v1/incidents/alerts?category=critical&view=current&include_count=true'
    assert client.get(path, headers=admin_headers).json()['total'] == 0
    check.check_type='icmp'
    db_session.commit()
    assert client.get(path, headers=admin_headers).json()['total'] == 1
    db_session.add(ZabbixHostImport(source_key='validation', host_id='1', device_id=device.id))
    db_session.commit()
    assert client.get(path, headers=admin_headers).json()['total'] == 0
