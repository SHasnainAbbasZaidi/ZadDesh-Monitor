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
from zaddesh_core.models.metrics import MetricRaw
from zaddesh_core.models.zabbix import ZabbixConnection
from zaddesh_core.security import decrypt_secret
from zaddesh_core.zabbix import ZabbixAPI, normalize_url


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
        if method == "item.get" and "filter" in data["params"]:
            assert data["params"]["filter"]["key_"] == ["icmpping"]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": remote[method]})

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
    assert client.get(root + "/problems", headers=admin_headers).json()[0]["name"] == "Link down"
    assert client.get(root + "/problems", headers=viewer_headers).status_code == 403

    device = client.post("/api/v1/devices", headers=admin_headers, json={
        "name": "Validation host", "ip": "192.0.2.10", "initial_check_types": ["icmp"],
    }).json()
    preferred = client.get(root + "/inventory", headers=admin_headers).json()
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
