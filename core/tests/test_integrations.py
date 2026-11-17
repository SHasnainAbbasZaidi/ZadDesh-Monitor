import base64
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from secrets import token_hex
from threading import Thread

import pytest

from zaddesh_core.api.v1.integrations import PROVIDERS, normalize_url
from zaddesh_core.config import settings
from zaddesh_core.models.integrations import IntegrationConnection
from zaddesh_core.security import decrypt_secret


@contextmanager
def endpoint(body, status=200):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            content = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.command, self.path, dict(self.headers), content))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def url(provider=""):
    return "http://127.0.0.1/api/v1/integrations" + ("/" + provider if provider else "")


def test_access_is_admin_only(client, viewer_headers, operator_headers):
    assert client.get(url()).status_code == 401
    for headers in (viewer_headers, operator_headers):
        assert client.get(url(), headers=headers).status_code == 403
        assert client.put(url("grafana"), headers=headers, json={"api_url": "http://localhost", "auth_type": "bearer", "secret": token_hex(20)}).status_code == 403
        assert client.post(url("prometheus/test"), headers=headers, json={"api_url": "http://localhost"}).status_code == 403
        assert client.delete(url("grafana"), headers=headers).status_code == 403


def test_credentials_encrypted_retained_and_removed(client, admin_headers, db_session, monkeypatch):
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    secret = token_hex(20)
    payload = {"api_url": "https://grafana.example/api", "auth_type": "bearer", "secret": secret}
    response = client.put(url("grafana"), headers=admin_headers, json=payload)
    assert response.status_code == 200, response.text
    assert secret not in response.text
    row = db_session.get(IntegrationConnection, "grafana")
    assert row.api_url == "https://grafana.example"
    assert secret not in row.encrypted_secret
    assert decrypt_secret(row.encrypted_secret) == secret
    payload["secret"] = ""
    assert client.put(url("grafana"), headers=admin_headers, json=payload).status_code == 200
    listing = client.get(url(), headers=admin_headers)
    assert len(listing.json()) == 7
    assert secret not in listing.text and row.encrypted_secret not in listing.text
    payload["api_url"] = "https://different.example"
    assert client.put(url("grafana"), headers=admin_headers, json=payload).status_code == 422
    assert row.api_url == "https://grafana.example"
    assert client.delete(url("grafana"), headers=admin_headers).status_code == 204
    assert db_session.get(IntegrationConnection, "grafana") is None


@pytest.mark.parametrize(("provider", "body"), [
    ("grafana", {"id": 1, "name": "Test organization"}),
    ("prometheus", {"status": "success", "data": {"resultType": "vector", "result": []}}),
    ("solarwinds", {"results": [{"NodeID": 1}]}),
    ("graylog", {"node_id": "test", "version": "6"}),
    ("loggly", {"rsid": {"id": "test"}}),
    ("logzio", {"accountId": 1, "accountName": "Test"}),
    ("opentelemetry", {}),
])
def test_provider_protocol_over_real_http(client, admin_headers, provider, body):
    secret = token_hex(20)
    auth_type = PROVIDERS[provider]["default_auth"]
    with endpoint(body) as (base, requests):
        payload = {"api_url": base, "auth_type": auth_type, "username": "api-user", "secret": secret}
        response = client.post(url(provider + "/test"), headers=admin_headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["ok"]
    assert secret not in response.text
    method, path, headers, content = requests[0]
    assert path.split("?")[0] == PROVIDERS[provider]["path"]
    if provider == "opentelemetry":
        assert method == "POST"
        assert json.loads(content) == {"resourceLogs": []}
    else:
        assert method == "GET"
    if auth_type == "bearer":
        assert headers["Authorization"] == "Bearer " + secret
    elif auth_type == "basic":
        assert base64.b64decode(headers["Authorization"].split()[1]).decode() == "api-user:" + secret
    elif auth_type == "graylog_token":
        assert base64.b64decode(headers["Authorization"].split()[1]).decode() == secret + ":token"
        assert headers["X-Requested-By"] == "ZadDesh"
    elif auth_type == "api_key":
        assert headers["X-API-TOKEN"] == secret
    else:
        assert "Authorization" not in headers


@pytest.mark.parametrize("status", [200, 302, 401, 403, 500])
def test_wrong_api_and_remote_errors_are_safe(client, admin_headers, status):
    secret = token_hex(20)
    with endpoint({"error": secret}, status) as (base, _):
        response = client.post(url("grafana/test"), headers=admin_headers, json={"api_url": base, "auth_type": "bearer", "secret": secret})
    assert response.status_code == 400
    assert secret not in response.text


def test_saved_secret_can_validate_without_exposure(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    secret = token_hex(20)
    with endpoint({"id": 1, "name": "Test"}) as (base, requests):
        payload = {"api_url": base, "auth_type": "bearer", "secret": secret}
        assert client.put(url("grafana"), headers=admin_headers, json=payload).status_code == 200
        payload["secret"] = ""
        response = client.post(url("grafana/test"), headers=admin_headers, json=payload)
    assert response.status_code == 200
    assert requests[0][2]["Authorization"] == "Bearer " + secret
    assert secret not in response.text


@pytest.mark.parametrize("value", ["file:///tmp/test", "https://user:password@example.com", "http://localhost/?token=test", "http://localhost/#test", "http://localhost:bad", "http://localhost/\nfoo"])
def test_invalid_urls_rejected(client, admin_headers, value):
    response = client.put(url("prometheus"), headers=admin_headers, json={"api_url": value})
    assert response.status_code == 422


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_endpoint_and_base_path_normalization(provider):
    path = PROVIDERS[provider]["path"]
    assert normalize_url("https://example.com/prefix" + path, path) == "https://example.com/prefix"
    assert normalize_url("https://example.com/prefix" + path.rsplit("/", 1)[0], path) == "https://example.com/prefix"


def test_credential_fields_and_transport_validated(client, admin_headers):
    for payload in [
        {"api_url": "http://localhost", "auth_type": "basic", "username": "invalid:name", "secret": "password"},
        {"api_url": "http://localhost", "auth_type": "api_key", "header_name": "Host", "secret": "test"},
        {"api_url": "http://localhost", "auth_type": "bearer", "secret": "bad\r\nheader"},
    ]:
        assert client.put(url("opentelemetry"), headers=admin_headers, json=payload).status_code == 422
    response = client.put("http://testserver/api/v1/integrations/prometheus", headers=admin_headers, json={"api_url": "http://localhost"})
    assert response.status_code == 403
