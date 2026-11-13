from datetime import timedelta
from secrets import token_hex

from zaddesh_core.api.v1.network_diagram import schedule_inventory
from zaddesh_core.config import settings
from zaddesh_core.incidents import utc_now
from zaddesh_core.models.infrastructure import Collector
from zaddesh_core.models.topology import TopologyLink, TopologyNode, TopologyRun
from zaddesh_core.security import hash_token
from zaddesh_core.topology import link_key


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
            if check_id == first:
                values["1.0.8802.1.1.2.1.4.2.1.3.0.1.1.1.4.192.0.2.5"] = "2"
                values.update({"1.0.8802.1.1.2.1.4.1.1.7.0.1.2": "Eth1",
                               "1.0.8802.1.1.2.1.4.1.1.5.0.1.2": "00:c8:8b:b9:d0:4e"})
        result = client.post(f"http://127.0.0.1/api/v1/collectors/topology-result/{task['run_id']}",
                             headers=collector, json={"lease": task["lease"], "values": values})
        assert result.status_code == 200 and result.json()["status"] == "complete"

    discover(first, "Gi0/1", "different-advertised-name", "Gi0/2", "1")
    discover(second, "GigabitEthernet0/2", "lab-switch", "GigabitEthernet0/1", "1")
    graph = client.get(route, headers=admin_headers)
    assert credentials["auth_password"] not in graph.text
    assert len(graph.json()["nodes"]) == 2 and len(graph.json()["links"]) == 1
    assert "00:c8:8b:b9:d0:4e" not in graph.text
    assert graph.json()["links"][0]["confidence"] == "high"
    node_id = next(node["id"] for node in graph.json()["nodes"] if node["device_id"] == first_device)
    connection = graph.json()["links"][0]
    peer = db_session.get(TopologyNode, connection["target"])
    peer.metadata_json = {**peer.metadata_json, "sys_name": "Site_B_C30"}
    duplicate = TopologyNode(diagram_id=identifier, label="Site_B_C30", key="neighbor:site_b_c30")
    db_session.add(duplicate)
    db_session.flush()
    db_session.add(TopologyLink(diagram_id=identifier, source_id=node_id, target_id=duplicate.id,
                               source_port="Gi0/1", target_port="Gi0/2", method="lldp",
                               key=link_key(node_id, "Gi0/1", duplicate.id, "Gi0/2")))
    db_session.commit()
    collapsed = client.get(route, headers=admin_headers).json()
    assert len(collapsed["nodes"]) == 2 and len(collapsed["links"]) == 1
    assert client.put(f"{route}/links/{connection['id']}", headers=admin_headers,
                      json={**connection, "configured_vlans": [20, 10, 20]}).status_code == 204
    assert client.put(f"{route}/nodes/{node_id}", headers=admin_headers, json={
        "label": "Edited switch", "kind": "switch",
    }).status_code == 204
    assert client.patch(route, headers=admin_headers, json={"positions": {str(node_id): [12, 34]}}).status_code == 200
    discover(first, "Gi0/1", "lab-router", "Gi0/2", "2", include_neighbor=False)
    assert client.get(route, headers=admin_headers).json()["links"][0]["status"] == "down"
    alerts = f"/api/v1/incidents/alerts?device_id={first_device}&type=link"
    alert = client.get(alerts, headers=viewer_headers).json()["alerts"][0]
    assert alert["status"] == "active"
    discover(first, "Gi0/1", "different-advertised-name", "Gi0/2", "1")
    updated_graph = client.get(route, headers=admin_headers).json()
    assert updated_graph["links"][0]["configured_vlans"] == [10, 20]
    assert updated_graph["links"][0]["method"] == "lldp"
    assert next(node for node in updated_graph["nodes"] if node["id"] == node_id)["label"] == "Edited switch"
    assert updated_graph["layout"]["positions"][str(node_id)] == [12, 34]
    assert client.get(alerts, headers=viewer_headers).json()["alerts"][0]["id"] == alert["id"]
    assert client.get(alerts, headers=viewer_headers).json()["alerts"][0]["status"] == "resolved"
    widget = "/api/v1/network-widgets/mine"
    assert client.put(widget, headers=viewer_headers, json={"diagram_id": identifier}).status_code == 404
    assert client.patch(route, headers=admin_headers, json={"published": True}).status_code == 200
    assert client.put(widget, headers=viewer_headers, json={"diagram_id": identifier}).status_code == 204
    assert len(client.get(widget, headers=viewer_headers).json()["diagram"]["links"]) == 1
    assert client.patch(route, headers=admin_headers, json={"published": False}).status_code == 200
    assert client.get(widget, headers=viewer_headers).json()["diagram"] is None


def test_saved_snmp_automatically_queues_one_map(client, db_session, admin_headers, monkeypatch):
    monkeypatch.setattr(settings.features, "network_diagram_beta", True)
    monkeypatch.setattr(settings.features, "topology_allowed_ranges", ["192.0.2.0/24"])
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))

    def add(ip):
        response = client.post("http://127.0.0.1/api/v1/devices", headers=admin_headers, json={
            "name": f"Switch {ip}", "ip": ip, "initial_check_types": [],
            "initial_snmp": {"params": {"version": "3", "security_level": "authNoPriv"},
                             "credentials": {"username": "diagram-lab", "auth_password": token_hex(16)}},
        })
        assert response.status_code == 201
        return client.get(f"/api/v1/checks?device_id={response.json()['id']}", headers=admin_headers).json()[0]["id"]

    now = utc_now()
    assert schedule_inventory(db_session, now) == 0
    denied = add("198.51.100.5")
    first = add("192.0.2.5")
    assert schedule_inventory(db_session, now) == 1
    assert schedule_inventory(db_session, now) == 0
    root = "/api/v1/network-diagram"
    [item] = client.get(root, headers=admin_headers).json()
    assert item["automatic"] and not item["published"]
    second = add("192.0.2.6")
    assert schedule_inventory(db_session, now) == 1
    runs = db_session.query(TopologyRun).filter_by(diagram_id=item["id"]).order_by(TopologyRun.id).all()
    assert [run.check_id for run in runs] == [first, second] and denied not in (first, second)
    runs[0].status = "complete"
    db_session.commit()
    assert schedule_inventory(db_session, now + timedelta(minutes=4)) == 0
    assert schedule_inventory(db_session, now + timedelta(minutes=5)) == 1
    assert len(client.get(root, headers=admin_headers).json()) == 1
