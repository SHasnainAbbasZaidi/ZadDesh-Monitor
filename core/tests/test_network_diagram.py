from secrets import token_hex

from zaddesh_core.config import settings
from zaddesh_core.models.infrastructure import Collector
from zaddesh_core.security import hash_token


def test_discovery_rbac_merge_publication_and_link_recovery(client, db_session, admin_headers, viewer_headers, monkeypatch):
    root = "/api/v1/network-diagram"
    assert client.get(root, headers=admin_headers).status_code == 404
    monkeypatch.setattr(settings.features, "network_diagram_beta", True)
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    monkeypatch.setattr(settings.features, "topology_allowed_ranges", ["192.0.2.0/24"])
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    token = token_hex(32)
    db_session.add(Collector(name="diagram-test", auth_token_hash=hash_token(token)))
    db_session.commit()
    collector = {"Authorization": f"Bearer {token}"}
    assert client.get(root, headers=viewer_headers).status_code == 403
    assert client.post(root, headers=viewer_headers, json={"name": "Forbidden"}).status_code == 403
    identifier = client.post(root, headers=admin_headers, json={"name": "Lab topology"}).json()["id"]
    route = f"{root}/{identifier}"
    credentials = {"username": "diagram-lab", "auth_password": token_hex(16)}

    def seed(name, ip):
        created = client.post("http://127.0.0.1/api/v1/devices", headers=admin_headers, json={
            "name": name, "ip": ip, "initial_check_types": [],
            "initial_snmp": {"params": {"version": "3", "security_level": "authNoPriv"}, "credentials": credentials},
        })
        assert created.status_code == 201
        device_id = created.json()["id"]
        return device_id, client.get(f"/api/v1/checks?device_id={device_id}", headers=admin_headers).json()[0]["id"]

    _, denied = seed("outside", "198.51.100.4")
    first_device, first = seed("lab-switch", "192.0.2.4")
    _, second = seed("lab-router", "192.0.2.5")
    assert client.post(route + "/discovery", headers=admin_headers, json={"checks": [denied]}).status_code == 422
    assert client.post(route + "/discovery", headers=viewer_headers, json={"checks": [first]}).status_code == 403

    def discover(check_id, local, neighbor, remote, state, include_neighbor=True):
        assert client.post(route + "/discovery", headers=admin_headers, json={"checks": [check_id]}).status_code == 202
        job = client.post("http://127.0.0.1/api/v1/collectors/topology-next", headers=collector)
        assert job.status_code == 200
        task = job.json()
        assert task["task"]["snmp_credentials"]["username"] == credentials["username"]
        values = {"1.3.6.1.2.1.2.2.1.2.1": local, "1.3.6.1.2.1.2.2.1.8.1": state}
        if include_neighbor:
            values.update({"1.0.8802.1.1.2.1.3.7.1.3.1": local,
                           "1.0.8802.1.1.2.1.4.1.1.7.0.1.1": remote,
                           "1.0.8802.1.1.2.1.4.1.1.9.0.1.1": neighbor})
        result = client.post(f"http://127.0.0.1/api/v1/collectors/topology-result/{task['run_id']}",
                             headers=collector, json={"lease": task["lease"], "values": values})
        assert result.status_code == 200 and result.json()["status"] == "complete"

    discover(first, "Gi0/1", "lab-router", "Gi0/2", "1")
    discover(second, "GigabitEthernet0/2", "lab-switch", "GigabitEthernet0/1", "1")
    graph = client.get(route, headers=admin_headers)
    assert credentials["auth_password"] not in graph.text
    assert len(graph.json()["nodes"]) == 2 and len(graph.json()["links"]) == 1
    assert graph.json()["links"][0]["confidence"] == "high"
    discover(first, "Gi0/1", "lab-router", "Gi0/2", "2", include_neighbor=False)
    assert client.get(route, headers=admin_headers).json()["links"][0]["status"] == "down"
    alerts = f"/api/v1/incidents/alerts?device_id={first_device}&type=link"
    alert = client.get(alerts, headers=viewer_headers).json()["alerts"][0]
    assert alert["status"] == "active"
    discover(first, "Gi0/1", "lab-router", "Gi0/2", "1")
    assert client.get(alerts, headers=viewer_headers).json()["alerts"][0]["id"] == alert["id"]
    assert client.get(alerts, headers=viewer_headers).json()["alerts"][0]["status"] == "resolved"
    widget = "/api/v1/network-widgets/mine"
    assert client.put(widget, headers=viewer_headers, json={"diagram_id": identifier}).status_code == 404
    assert client.patch(route, headers=admin_headers, json={"published": True}).status_code == 200
    assert client.put(widget, headers=viewer_headers, json={"diagram_id": identifier}).status_code == 204
    assert len(client.get(widget, headers=viewer_headers).json()["diagram"]["links"]) == 1
    assert client.patch(route, headers=admin_headers, json={"published": False}).status_code == 200
    assert client.get(widget, headers=viewer_headers).json()["diagram"] is None
