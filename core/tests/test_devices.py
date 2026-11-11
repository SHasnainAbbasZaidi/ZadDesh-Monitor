import json
import secrets

from zaddesh_core.config import settings
from zaddesh_core.models.infrastructure import Collector, DeviceCredential, Site
from zaddesh_core.security import decrypt_secret, hash_token


def test_create_and_list_device(client, admin_headers):
    # 1. Create a site
    site_res = client.post(
        "/api/v1/sites",
        json={"name": "Test Site Alpha", "slug": "test-alpha"},
        headers=admin_headers,
    )
    assert site_res.status_code == 201
    site_id = site_res.json()["id"]

    # 2. Create device
    dev_res = client.post(
        "/api/v1/devices",
        json={
            "name": "core-switch-test",
            "ip": "192.168.1.1",
            "site_id": site_id,
            "vendor": "Cisco",
            "model": "Catalyst 9300",
            "initial_check_types": ["icmp"],
        },
        headers=admin_headers,
    )
    assert dev_res.status_code == 201
    dev_data = dev_res.json()
    device_id = dev_data["id"]
    assert dev_data["name"] == "core-switch-test"
    assert dev_data["ip"] == "192.168.1.1"

    # 3. Verify check was auto-provisioned
    chk_res = client.get(f"/api/v1/checks?device_id={device_id}", headers=admin_headers)
    assert chk_res.status_code == 200
    checks = chk_res.json()
    assert len(checks) == 1
    assert checks[0]["check_type"] == "icmp"

    # 4. Get device stats
    stats_res = client.get("/api/v1/devices/stats", headers=admin_headers)
    assert stats_res.status_code == 200
    stats = stats_res.json()
    assert stats["total_devices"] >= 1


def test_invalid_snmp_does_not_create_device(client, admin_headers):
    for params in [
        {"credential": "lab_switch", "version": "1", "operation": "getbulk"},
        {"credential": "lab_switch", "version": "2c", "community": "test-only"},
    ]:
        response = client.post("/api/v1/devices", headers=admin_headers, json={
            "name": "invalid-initial-snmp",
            "ip": "192.0.2.10",
            "initial_snmp": {"params": params},
        })
        assert response.status_code == 422
    devices = client.get("/api/v1/devices?query=invalid-initial-snmp", headers=admin_headers)
    assert devices.json() == []


def test_snmp_device_credentials_are_encrypted_and_collector_scoped(client, db_session, admin_headers, monkeypatch):
    monkeypatch.setattr(settings.security, "encryption_key", secrets.token_hex(32))
    shared_token, collector_token, other_token = (secrets.token_hex(32) for _ in range(3))
    monkeypatch.setattr(settings.collector, "shared_secret", shared_token)
    sites = [Site(name="SNMP lab", slug="snmp-lab"), Site(name="Other site", slug="other")]
    db_session.add_all(sites)
    db_session.flush()
    local = Collector(name="snmp-local", site_id=sites[0].id, auth_token_hash=hash_token(collector_token))
    other = Collector(name="snmp-other", site_id=sites[1].id, auth_token_hash=hash_token(other_token))
    db_session.add_all([local, other])
    db_session.flush()
    passwords = {"username": "lab-user", "auth_password": secrets.token_hex(12), "priv_password": secrets.token_hex(12)}
    payload = {
        "name": "snmp-direct", "ip": "192.0.2.10", "hostname": "snmp.example.test",
        "site_id": sites[0].id, "initial_check_types": [],
        "initial_snmp": {
            "connect_to": "dns", "credentials": passwords,
            "params": {"version": "3", "context_name": "routers", "operation": "getbulk", "max_repetitions": 10},
        },
    }
    response = client.post("http://127.0.0.1/api/v1/devices", json=payload, headers=admin_headers)
    assert response.status_code == 201
    device_id = response.json()["id"]
    stored = db_session.query(DeviceCredential).filter_by(device_id=device_id).one()
    assert all(secret not in stored.encrypted_data for secret in passwords.values())
    assert json.loads(decrypt_secret(stored.encrypted_data)) == passwords
    checks = client.get(f"/api/v1/checks?device_id={device_id}", headers=admin_headers)
    check = checks.json()[0]
    assert check["target"] == "snmp.example.test"
    assert "stored_credential_id" not in check["params"]
    assert all(secret not in checks.text and secret not in response.text for secret in passwords.values())

    def tasks(collector_id, token, **headers):
        return client.get(f"http://127.0.0.1/api/v1/collectors/{collector_id}/tasks", headers={
            "Authorization": f"Bearer {token}", **headers,
        })

    allowed = tasks(local.id, collector_token)
    assert allowed.headers["cache-control"] == "no-store"
    task = allowed.json()["tasks"][0]
    assert task["snmp_credentials"] == passwords
    assert task["params"]["context_name"] == "routers"
    assert task["params"]["max_repetitions"] == 10
    assert tasks(other.id, collector_token).status_code == 403
    assert tasks(other.id, other_token).json()["tasks"] == []
    assert tasks(local.id, shared_token).json()["tasks"] == []
    assert tasks(local.id, collector_token, Host="insecure.example.test").json()["tasks"] == []
    assert client.post("/api/v1/devices", json=payload, headers={
        **admin_headers, "Host": "insecure.example.test",
    }).status_code == 403

    deleted = client.delete(f"/api/v1/checks/{check['id']}", headers=admin_headers)
    assert deleted.status_code == 204
    assert db_session.query(DeviceCredential).filter_by(device_id=device_id).count() == 0

