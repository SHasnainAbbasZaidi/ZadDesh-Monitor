from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from secrets import token_hex
import ssl
from threading import Thread

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from zaddesh_core import zabbix
from zaddesh_core.api.v1 import zabbix as routes
from zaddesh_core.config import settings
from zaddesh_core.incidents import utc_now
from zaddesh_core.models.infrastructure import Device, DeviceCredential
from zaddesh_core.models.metrics import MetricRaw
from zaddesh_core.models.zabbix import ZabbixConnection
from zaddesh_core.security import decrypt_secret, encrypt_secret
from zaddesh_core.zabbix import ZabbixAPI, normalize_url


def test_probe_history_uses_ping_items_matches_targets_and_converts_rtt(client, admin_headers, viewer_headers, monkeypatch):
    clock = str(int(datetime.now(timezone.utc).timestamp()) - 30)

    class Remote:
        def rows(self, method, params):
            if method == "item.get":
                assert params["hostids"] == ["7"]
                return [
                    {"itemid": "1", "hostid": "7", "key_": "icmpping", "delay": "30s", "value_type": "3"},
                    {"itemid": "2", "hostid": "7", "key_": "icmppingsec[other-target]", "value_type": "0"},
                    {"itemid": "3", "hostid": "7", "key_": "icmppingsec", "value_type": "0"},
                ]
            assert method == "history.get" and params["time_till"] - params["time_from"] == 3600
            assert params["itemids"] == (["1"] if params["history"] == 3 else ["3"])
            if params["history"] == 3:
                return [{"itemid": "1", "clock": clock, "value": "0"}]
            return [{"itemid": "3", "clock": clock, "value": "0.012"}, {"itemid": "2", "clock": clock, "value": "5"}]

    @contextmanager
    def connection(_):
        yield Remote()

    monkeypatch.setattr(routes, "saved_api", connection)
    path = "/api/v1/zabbix/probe-history"
    assert client.post(path, headers=viewer_headers, json={"host_ids": ["7"]}).status_code == 403
    result = client.post(path, headers=admin_headers, json={"host_ids": ["7"]})
    assert result.status_code == 200
    channel = result.json()["channels"]["7"]
    assert channel["interval_seconds"] == 30
    assert [point["value"] for point in channel["latency"]] == [12]
    assert len(channel["failures"]) == 1


def test_read_only_connection_inventory_problems_and_validation(client, db_session, admin_headers, viewer_headers, monkeypatch):
    token = token_hex(32)
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    sample_time = utc_now().replace(microsecond=0)
    stamp = str(int(sample_time.replace(tzinfo=timezone.utc).timestamp()))
    remote = {
        "apiinfo.version": "7.4.1",
        "host.get": [{"hostid": "7", "name": "Validation host", "host": "validation-host", "status": "0",
                      "interfaces": [{"interfaceid": "11", "ip": "192.0.2.10", "useip": "1"}]}],
        "problem.get": [{"eventid": "9", "name": "Link down", "severity": "4", "clock": stamp, "acknowledged": "0"}],
        "item.get": [{"lastvalue": "1", "lastclock": stamp, "interfaceid": "11"}],
    }
    methods = []

    def upstream(request: httpx.Request):
        data = json.loads(request.content)
        method = data["method"]
        methods.append(method)
        assert request.url.path == "/zabbix/api_jsonrpc.php"
        assert request.url.scheme == "http" and "sni_hostname" not in request.extensions
        assert request.headers.get("authorization") == (None if method == "apiinfo.version" else "Bearer " + token)
        if method == "item.get" and "key_" in data["params"].get("filter", {}):
            assert data["params"]["filter"]["key_"] == ["icmpping"]
        if method == "item.get" and data["params"].get("hostids") == ["9"] and "search" not in data["params"]:
            assert "limit" not in data["params"]
        result = remote[method]
        if method == "host.get" and "hostids" in data["params"]:
            result = [row for row in result if row["hostid"] in data["params"]["hostids"]]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})

    @contextmanager
    def connect(url, key, version=""):
        with httpx.Client(transport=httpx.MockTransport(upstream)) as transport:
            yield ZabbixAPI(transport, normalize_url(url), key, version)

    monkeypatch.setattr(routes, "connect", connect)
    root = "http://127.0.0.1/api/v1/zabbix"
    assert client.get(root + "/connection", headers=viewer_headers).status_code == 403
    assert client.put(root + "/connection", headers=viewer_headers, json={
        "api_url": "http://192.0.2.19/zabbix", "token": token,
    }).status_code == 403
    response = client.put(root + "/connection", headers=admin_headers, json={
        "api_url": "http://192.0.2.19/zabbix", "token": token,
    })
    assert response.status_code == 200 and token not in response.text
    assert response.json() == {"configured": True, "api_url": "http://192.0.2.19/zabbix/api_jsonrpc.php", "version": "7.4.1"}
    saved = db_session.get(ZabbixConnection, 1)
    assert saved.encrypted_token != token and decrypt_secret(saved.encrypted_token) == token
    assert client.get(root + "/hosts", headers=admin_headers).json()["hosts"][0]["name"] == "Validation host"
    remote["host.get"].append({"hostid": "8", "name": "New external device", "status": "0"})
    assert len(client.get(root + "/hosts", headers=admin_headers).json()["hosts"]) == 2
    assert client.get(root + "/problems", headers=admin_headers).json() == []
    assert client.get(root + "/problems", headers=viewer_headers).status_code == 403

    device = client.post("/api/v1/devices", headers=admin_headers, json={
        "name": "Validation host", "ip": "192.0.2.10", "initial_check_types": ["icmp"], "is_priority": True,
    }).json()
    preferred = client.get(root + "/inventory", headers=admin_headers).json()
    assert client.get(root + "/problems", headers=admin_headers).json()[0]["name"] == "Link down"
    assert preferred["hosts"][0]["local_device_ids"] == [device["id"]]
    assert preferred["hosts"][1]["local_device_ids"] == []
    assert client.get(f"/api/v1/devices/{device['id']}", headers=admin_headers).status_code == 200
    assert client.get(root + "/inventory", headers=viewer_headers).status_code == 403
    check = client.get(f"/api/v1/checks?device_id={device['id']}", headers=admin_headers).json()[0]
    target = f"{root}/validation?device_id={device['id']}&host_id=7"
    assert client.get(target, headers=admin_headers).json()["status"] == "unavailable"
    db_session.add(MetricRaw(device_id=device["id"], check_id=check["id"], metric_key="icmp.availability",
                             timestamp=sample_time + timedelta(seconds=5), value=1))
    db_session.commit()
    assert client.get(target, headers=admin_headers).json()["status"] == "match"
    remote["item.get"][0]["lastvalue"] = "0"
    comparison = client.get(target, headers=admin_headers).json()
    assert comparison["status"] == "mismatch" and comparison["skew_seconds"] == 5
    remote["item.get"][0]["interfaceid"] = "12"
    assert client.get(target, headers=admin_headers).json()["status"] == "unavailable"
    remote["host.get"][0]["interfaces"][0]["ip"] = "192.0.2.11"
    assert client.get(target, headers=admin_headers).status_code == 422
    imported_host = {
        "hostid": "9", "name": "Imported switch", "host": "imported-switch", "status": "0",
        "inventory": {"vendor": "Cisco", "model": "N3K-C3064PQ-10GX"},
        "macros": [{"macro": "{$SNMP_USER}", "type": "0", "value": "zabbix-lab"}],
        "interfaces": [
            {"interfaceid": "19", "type": "2", "main": "1", "useip": "1", "ip": "192.0.2.30",
             "dns": "switch.example.test", "port": "1161", "details": {
                "version": "3", "securitylevel": "2", "authprotocol": "3", "privprotocol": "1", "contextname": "infra",
                "securityname": "{$SNMP_USER}", "authpassphrase": "fixture-auth-secret", "privpassphrase": "fixture-priv-secret",
             }},
            {"interfaceid": "20", "type": "2", "useip": "1", "ip": "192.0.2.31", "port": "161",
             "details": {"version": "2", "community": "{$HIDDEN}"}},
        ],
    }
    remote["host.get"].append(imported_host)
    remote["item.get"] = [
        {"type": "20", "key_": "net.status", "name": "Interface state", "interfaceid": "19", "delay": "2m",
         "timeout": "4s", "status": "0", "snmp_oid": "get[1.3.6.1.2.1.2.2.1.8.1]"},
        {"type": "3", "key_": "icmpping", "name": "Ping", "interfaceid": "19", "delay": "1m", "timeout": "3s", "status": "1"},
        {"type": "20", "key_": "sys", "interfaceid": "20", "delay": "60", "status": "0", "snmp_oid": "1.3.6.1.2.1.1.3.0"},
        {"type": "0", "key_": "agent.ping"},
    ]
    import_route = root + "/hosts/9/import"
    assert client.post(import_route, headers=viewer_headers).status_code == 403
    import_response = client.post(import_route, headers=admin_headers)
    assert import_response.status_code == 200 and "fixture-auth-secret" not in import_response.text
    result = import_response.json()
    assert result["status"] == "added" and result["checks_created"] == 2 and sum(result["skipped"].values()) == 2
    device_id = result["device_id"]
    imported = client.get(f"/api/v1/devices/{device_id}", headers=admin_headers).json()
    assert (imported["name"], imported["ip"], imported["hostname"]) == ("Imported switch", "192.0.2.30", "switch.example.test")
    assert (imported["vendor"], imported["model"]) == ("Cisco", "N3K-C3064PQ-10GX")
    imported_checks = client.get(f"/api/v1/checks?device_id={device_id}", headers=admin_headers).json()
    checks_by_type = {item["check_type"]: item for item in imported_checks}
    snmp_check = checks_by_type["snmp"]
    assert (snmp_check["target"], snmp_check["interval_seconds"], snmp_check["timeout_seconds"]) == ("192.0.2.30", 120, 4)
    assert snmp_check["params"] == {"version": "3", "operation": "get", "oid": "1.3.6.1.2.1.2.2.1.8.1",
                                    "port": 1161, "security_level": "authPriv", "auth_protocol": "sha256",
                                    "privacy_protocol": "aes128", "context_name": "infra"}
    assert not checks_by_type["icmp"]["is_enabled"]
    credential = db_session.query(DeviceCredential).filter_by(device_id=device_id).one()
    assert "fixture-auth-secret" not in credential.encrypted_data
    assert json.loads(decrypt_secret(credential.encrypted_data)) == {
        "username": "zabbix-lab", "auth_password": "fixture-auth-secret", "priv_password": "fixture-priv-secret",
    }
    imported_host["interfaces"][0]["ip"] = "192.0.2.32"
    repeated = client.post(import_route, headers=admin_headers).json()
    assert repeated["status"] == "already_added" and repeated["device_id"] == device_id
    hosts = client.get(root + "/hosts", headers=admin_headers)
    assert "fixture-auth-secret" not in hosts.text and hosts.json()["hosts"][-1]["local_device_ids"] == [device_id]
    assert set(methods) == {"apiinfo.version", "host.get", "problem.get", "item.get"}
    assert client.delete(root + "/connection", headers=admin_headers).status_code == 204
    assert not client.get(root + "/connection", headers=admin_headers).json()["configured"]


def test_connection_blocks_local_metadata_and_insecure_transport(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    for url in ("http://169.254.169.254", "https://127.0.0.1", "ftp://192.0.2.19"):
        response = client.put("http://127.0.0.1/api/v1/zabbix/connection", headers=admin_headers,
                              json={"api_url": url, "token": token_hex(32)})
        assert response.status_code == 422
    assert client.put("/api/v1/zabbix/connection", headers=admin_headers,
                      json={"api_url": "https://192.0.2.19", "token": token_hex(32)}).status_code == 403


def test_pinned_https_connection_verifies_original_zabbix_hostname(tmp_path, monkeypatch):
    hostname = "zabbix.example.test"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    identity = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(identity).issuer_name(identity).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "zabbix.crt", tmp_path / "zabbix.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Host"].startswith(hostname)
            assert data["method"] == "apiinfo.version" and "Authorization" not in self.headers
            content = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "7.4.1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(content)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert_path, key_path)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            monkeypatch.setattr(zabbix, "pinned_target", lambda url: url.copy_with(host="127.0.0.1"))
            with httpx.Client(verify=ssl.create_default_context(cafile=str(cert_path)), trust_env=False) as transport:
                api = ZabbixAPI(transport, normalize_url(f"https://{hostname}:{server.server_port}"), "")
                assert api.detect_version() == "7.4.1"
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_http_connection_reads_hosts_with_bearer_token(monkeypatch):
    token = token_hex(32)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Host"] == f"zabbix.example.test:{self.server.server_port}"
            assert self.headers["Authorization"] == f"Bearer {token}"
            assert data["method"] == "host.get"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"jsonrpc":"2.0","id":1,"result":[{"hostid":"7"}]}')

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            monkeypatch.setattr(zabbix, "pinned_target", lambda url: url.copy_with(host="127.0.0.1"))
            with httpx.Client(trust_env=False, timeout=2) as transport:
                api = ZabbixAPI(transport, normalize_url(f"http://zabbix.example.test:{server.server_port}"), token, "7.4.1")
                assert api.rows("host.get", {"output": ["hostid"]}) == [{"hostid": "7"}]
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_agent_availability_and_incident_feed(client, db_session, admin_headers, viewer_headers, monkeypatch):
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    token = token_hex(32)
    db_session.add(ZabbixConnection(id=1, api_url="http://192.0.2.19/api_jsonrpc.php",
                                    encrypted_token=encrypt_secret(token), version="7.0.32"))
    db_session.add_all([
        Device(name="Local copy", ip="192.0.2.21", status="unknown"),
        Device(name="Local-only device", ip="192.0.2.44", status="warning"),
    ])
    db_session.commit()
    now = utc_now().replace(microsecond=0, tzinfo=timezone.utc)
    clock = int(now.timestamp()) - 10

    def record(identifier, name, age=0, value="1"):
        return {"eventid": identifier, "name": name, "severity": "5", "clock": str(clock - age), "value": value,
                "hosts": [{"name": "Agent fixture"}]}

    problems = [record("1", "Unavailable by ICMP ping"), record("2", "Agent disconnected"),
                record("3", "Zabbix agent is not available"), record("4", "Downlink down", 9 * 3600)]

    def upstream(request):
        data = json.loads(request.content)
        method, params = data["method"], data["params"]
        if method == "host.get":
            rows = [{"hostid": str(i), "name": f"Agent {i}", "status": "0", "active_available": active,
                     "interfaces": [{"available": "0", "ip": f"192.0.2.{20 + i}", "useip": "1", "main": "1"}]}
                    for i, active in enumerate(("1", "0", "0"), 1)]
        elif method == "item.get":
            if "search" in params:
                rows = [{"hostid": "1", "key_": "system.objectid[sysObjectID.0]", "lastvalue": ".1.3.6.1.4.1.9.12.3.1.3.1163"}]
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": rows})
            assert params["hostids"] == ["2", "3"]
            rows = [{"hostid": "2", "lastclock": str(clock), "delay": "1m"},
                    {"hostid": "3", "lastclock": str(clock - 3600), "delay": "1m"}]
        elif method == "problem.get":
            rows = problems
        else:
            rows = problems + [record("5", "Uplink down", value="0")]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": rows})

    @contextmanager
    def connect(url, key, version=""):
        with httpx.Client(transport=httpx.MockTransport(upstream)) as transport:
            yield ZabbixAPI(transport, normalize_url(url), key, version)

    monkeypatch.setattr(routes, "connect", connect)
    rows = client.get("/api/v1/zabbix/inventory", headers=admin_headers).json()["hosts"]
    assert rows[0]["active_available"] == "1"
    assert (rows[0]["vendor"], rows[0]["model"]) == ("Cisco", "N3K-C3064PQ-10GX")
    assert rows[1]["agent_reporting_at"]
    assert rows[2]["agent_reporting_at"] is None
    fleet = client.get("/api/v1/zabbix/fleet", headers=viewer_headers).json()
    assert fleet["stats"] == {"total_devices": 4, "up": 2, "warning": 1, "down": 0, "unknown": 1, "unmanaged": 0}
    assert "Local copy" not in {row["name"] for row in fleet["devices"]}
    assert next(row for row in fleet["devices"] if row["name"] == "Agent 2")["status_label"] == "Agent reporting"
    base = "/api/v1/incidents"
    feed = base + "/zabbix"
    critical = client.get(feed + "?category=critical", headers=viewer_headers).json()["entries"]
    assert [(row["id"], row["category"], row["device"]) for row in critical] == [("1", "device_offline", "Agent fixture")]
    assert {row["id"] for row in client.get(feed, headers=viewer_headers).json()["entries"]} == {"1", "3"}
    events = client.get(feed + "?kind=events", headers=viewer_headers).json()["entries"]
    assert next(row for row in events if row["id"] == "5")["status"] == "resolved"
    assert client.get(feed, headers={}).status_code == 401
    start, end = now - timedelta(minutes=1), now
    result = client.post(base + "/alerts/dismiss", headers=admin_headers, json={"start": start.isoformat(), "end": end.isoformat()})
    assert result.json() == {"removed": 0, "zabbix_range_applied": True}
    assert client.get(feed, headers=viewer_headers).json()["entries"] == []
    assert client.get(feed + "?kind=events", headers=viewer_headers).json()["entries"]
    removal = {"device_ids": [identifier for row in rows for identifier in row["local_device_ids"]],
               "zabbix_host_ids": [row["hostid"] for row in rows]}
    endpoint = "/api/v1/devices/bulk-delete"
    assert client.post(endpoint, headers=viewer_headers, json=removal).status_code == 403
    assert client.post(endpoint, headers=admin_headers, json=removal).json() == {"deleted": 1, "zabbix_hidden": 3}
    assert client.post(endpoint, headers=admin_headers, json=removal).json() == {"deleted": 0, "zabbix_hidden": 0}
    assert client.get("/api/v1/zabbix/inventory", headers=admin_headers).json()["hosts"] == []
    assert client.get("/api/v1/zabbix/fleet", headers=viewer_headers).json()["stats"]["total_devices"] == 1
    assert client.get(feed + "?kind=events", headers=viewer_headers).json()["entries"] == []
