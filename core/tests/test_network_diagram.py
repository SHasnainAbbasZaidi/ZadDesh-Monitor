from datetime import timedelta
from secrets import token_hex

from zaddesh_core.api.v1.network_diagram import schedule_inventory
from zaddesh_core.config import settings
from zaddesh_core.incidents import utc_now
from zaddesh_core.models.alerts import Alert
from zaddesh_core.models.infrastructure import Collector, Device
from zaddesh_core.models.topology import TopologyLink, TopologyNode, TopologyRun
from zaddesh_core.security import hash_token
from zaddesh_core.topology import link_key, merge_observation, parse_interfaces


def test_snmp_interface_speed_baseline_and_bridge_vlan_mapping():
    at = utc_now()
    values = {
        "1.3.6.1.2.1.31.1.1.1.1.39": "Ethernet1/39",
        "1.3.6.1.2.1.31.1.1.1.15.39": "1000",
        "1.3.6.1.2.1.2.2.1.7.39": "1",
        "1.3.6.1.2.1.2.2.1.8.39": "1",
        "1.3.6.1.2.1.2.2.1.14.39": "2",
        "1.3.6.1.2.1.17.1.4.1.2.2": "39",
        "1.3.6.1.2.1.17.7.1.4.3.1.2.10": "40",
        "1.3.6.1.2.1.17.7.1.4.3.1.2.20": "40",
        "1.3.6.1.2.1.17.7.1.4.3.1.4.10": "40",
        "1.3.6.1.2.1.17.7.1.4.5.1.1.2": "10",
    }
    port = parse_interfaces(values, {"39": {"speed": 10_000_000_000}}, at)["39"]
    assert (port["status"], port["admin_status"], port["in_errors"]) == ("up", "up", 2)
    assert (port["speed"], port["expected_speed"]) == (1_000_000_000, 10_000_000_000)
    assert (port["pvid"], port["egress_vlans"], port["untagged_vlans"]) == (10, [10, 20], [10])


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


def test_findings_builds_its_own_map_with_v2c_and_v3(client, db_session, admin_headers, monkeypatch):
    monkeypatch.setattr(settings.features, "network_diagram_beta", True)
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    monkeypatch.setattr(settings.features, "topology_allowed_ranges", ["192.0.2.0/24"])
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    token, community = token_hex(32), token_hex(16)
    db_session.add(Collector(name="findings-test", auth_token_hash=hash_token(token)))
    db_session.commit()
    collector_headers = {"Authorization": f"Bearer {token}"}
    collector = "http://127.0.0.1/api/v1/collectors"
    root = "/api/v1/network-diagram"

    def add(name, ip, params, credentials):
        response = client.post("http://127.0.0.1/api/v1/devices", headers=admin_headers, json={
            "name": name, "ip": ip, "initial_check_types": [],
            "initial_snmp": {"params": params, "credentials": credentials},
        })
        assert response.status_code == 201
        return client.get(f"/api/v1/checks?device_id={response.json()['id']}", headers=admin_headers).json()[0]["id"]

    v3 = add("Managed switch", "192.0.2.41", {"version": "3", "security_level": "authNoPriv"},
             {"username": "findings-lab", "auth_password": token_hex(16)})
    v2c = add("Legacy switch", "192.0.2.42", {"version": "2c"}, {"community": community})
    now = utc_now()
    assert schedule_inventory(db_session, now) == 1
    [automatic] = client.get(root, headers=admin_headers).json()
    auto_url = f"{root}/{automatic['id']}"
    source = db_session.query(Device).filter_by(ip="192.0.2.41").one()
    merge_observation(db_session, automatic["id"], source, {
        "1.3.6.1.2.1.1.5.0": "Managed switch", "1.3.6.1.2.1.2.2.1.2.1": "Gi0/1",
        "1.3.6.1.2.1.2.2.1.8.1": "1", "1.0.8802.1.1.2.1.3.7.1.3.1": "Gi0/1",
        "1.0.8802.1.1.2.1.4.1.1.7.0.1.1": "Gi0/2", "1.0.8802.1.1.2.1.4.1.1.9.0.1.1": "Legacy switch",
    })
    db_session.commit()
    saved_link = client.get(auto_url, headers=admin_headers).json()["links"][0]
    assert client.put(f"{auto_url}/links/{saved_link['id']}", headers=admin_headers,
                      json={**saved_link, "configured_vlans": [20]}).status_code == 204
    assert client.put(f"{auto_url}/nodes/0", headers=admin_headers, json={"label": "Manually added node"}).status_code == 204
    assert schedule_inventory(db_session, now, findings=True) == 2
    assert schedule_inventory(db_session, now, findings=True) == 0
    automatic, findings = client.get(root, headers=admin_headers).json()
    assert automatic["automatic"] and not automatic["findings"]
    assert findings["name"] == "ZadDesh Findings" and findings["findings"] and not findings["automatic"]
    auto_url, findings_url = f"{root}/{automatic['id']}", f"{root}/{findings['id']}"
    cached = client.get(findings_url, headers=admin_headers).json()
    assert len(cached["nodes"]) == 2 and all(node["cached_observation"] for node in cached["nodes"])
    assert cached["links"][0]["status"] == "unknown" and cached["links"][0]["reporting_ends"] == 0
    assert cached["links"][0]["configured_vlans"] == []
    assert client.post(auto_url + "/discovery", headers=admin_headers, json={"checks": [v2c]}).status_code == 422
    assert client.patch(auto_url, headers=admin_headers, json={"layout": "site", "positions": {"1": [12, 34]}}).status_code == 200
    original = client.get(auto_url, headers=admin_headers).json()
    automatic_job = client.post(collector + "/topology-next", headers=collector_headers).json()
    assert automatic_job["task"]["check_id"] == v3
    assert client.post(collector + "/topology-next", headers=collector_headers).status_code == 204

    for _ in range(2):
        job = client.post(collector + "/topology-next/findings", headers=collector_headers).json()
        legacy = job["task"]["check_id"] == v2c
        assert job["task"]["params"]["version"] == ("2c" if legacy else "3")
        if legacy:
            assert job["task"]["snmp_credentials"]["community"] == community
        local, remote = ("Gi0/2", "Gi0/1") if legacy else ("Gi0/1", "Gi0/2")
        values = {"1.3.6.1.2.1.2.2.1.2.1": local, "1.3.6.1.2.1.2.2.1.8.1": "2",
                  "1.0.8802.1.1.2.1.3.7.1.3.1": local, "1.0.8802.1.1.2.1.4.1.1.7.0.1.1": remote,
                  "1.0.8802.1.1.2.1.4.1.1.9.0.1.1": "Managed switch" if legacy else "Legacy switch"}
        result = client.post(f"{collector}/topology-result/{job['run_id']}", headers=collector_headers,
                             json={"lease": job["lease"], "values": values})
        assert result.status_code == 200 and result.json()["status"] == "complete"

    response = client.get(findings_url, headers=admin_headers)
    assert community not in response.text
    graph = response.json()
    assert graph["layout"]["mode"] == "expanded"
    assert {node["snmp_version"] for node in graph["nodes"]} == {"2c", "3"}
    assert all(not node["cached_observation"] for node in graph["nodes"])
    [link] = graph["links"]
    assert link["reporting_ends"] == 2 and link["status"] == "down"
    assert db_session.query(Alert).count() == 0
    assert client.get(auto_url, headers=admin_headers).json() == original
