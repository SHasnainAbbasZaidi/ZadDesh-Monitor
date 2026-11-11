from datetime import datetime, timedelta, timezone
import secrets

from fastapi.testclient import TestClient
import jwt

from zaddesh_core.config import settings, AppSettings
from zaddesh_core.main import app
from zaddesh_core.models.auth import ApiToken, User
from zaddesh_core.security import generate_api_token


def bearer(value):
    return {"Authorization": f"Bearer {value}"}


def test_registration_requires_enrollment_and_cannot_replace_key(client, monkeypatch):
    bootstrap, token, second = (secrets.token_hex(32) for _ in range(3))
    monkeypatch.setattr(settings.collector, "enrollment_token", bootstrap)
    route = "/api/v1/collectors/register"
    payload = {"name": "test collector", "auth_token": token}
    assert client.post(route, json=payload).status_code == 401
    registered = client.post(route, json=payload, headers=bearer(bootstrap))
    assert registered.status_code == 201
    assert "auth_token" not in registered.json()
    cid = registered.json()["id"]
    assert client.post(route, json={**payload, "auth_token": second}, headers=bearer(bootstrap)).status_code == 409
    assert client.get(f"/api/v1/collectors/{cid}/tasks", headers=bearer(token)).status_code == 200
    assert client.get(f"/api/v1/collectors/{cid + 1}/tasks", headers=bearer(token)).status_code == 403
    assert client.post("/api/v1/metrics/ingest", json={"collector_id": cid + 1, "points": []}, headers=bearer(token)).status_code == 403


def test_user_auth_cannot_ingest_or_configure_probes(client, viewer_headers, operator_headers, db_session):
    for username, headers in (("viewer", viewer_headers), ("operator", operator_headers)):
        assert client.post("/api/v1/metrics/ingest", json={"points": []}, headers=headers).status_code == 401
        assert client.post("/api/v1/auth/tokens", json={"name": "test"}, headers=headers).status_code == 403
        account = db_session.query(User).filter_by(username=username).one()
        token, prefix, digest = generate_api_token()
        db_session.add(ApiToken(user_id=account.id, name="legacy", prefix=prefix, token_hash=digest))
        db_session.commit()
        assert client.post("/api/v1/metrics/ingest", json={"points": []}, headers=bearer(token)).status_code == 401
        assert client.post("/api/v1/devices", json={"name": "forbidden", "ip": "127.0.0.1"}, headers=headers).status_code == 403


def test_spa_never_serves_files_outside_dist(client):
    assert client.get("/%2e%2e/%2e%2e/config/zaddesh.yaml").status_code == 404
    assert client.get("/api/v1/unknown").status_code == 404
    assert client.get("/healthz").headers["x-content-type-options"] == "nosniff"


def test_jwt_signature_and_required_claims(client):
    claims = {"sub": "admin", "user_id": 1, "iat": datetime.now(timezone.utc),
              "exp": datetime.now(timezone.utc) + timedelta(minutes=2)}
    forged = jwt.encode(claims, secrets.token_hex(32), algorithm="HS256")
    assert client.get("/api/v1/auth/users", headers=bearer(forged)).status_code == 401
    del claims["exp"]
    incomplete = jwt.encode(claims, settings.security.jwt_secret, algorithm="HS256")
    assert client.get("/api/v1/auth/users", headers=bearer(incomplete)).status_code == 401


def test_environment_overrides_yaml_and_defaults_are_independent(monkeypatch, tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("server: {port: 8999}", encoding="utf-8")
    monkeypatch.setenv("ZADDESH_CONFIG_FILE", str(path))
    monkeypatch.setenv("ZADDESH_SERVER__PORT", "8998")
    assert AppSettings.load().server.port == 8998
    monkeypatch.delenv("ZADDESH_SECURITY__JWT_SECRET", raising=False)
    assert AppSettings.load().security.jwt_secret != AppSettings.load().security.jwt_secret


def test_remote_setup_and_login_attempt_limits(client):
    with TestClient(app, client=("192.0.2.15", 51001)) as remote:
        setup = remote.post("/api/v1/auth/setup", json={"username": "remote", "email": "remote@example.com", "password": secrets.token_hex(16)})
        assert setup.status_code == 403
        for _ in range(12):
            assert remote.post("/api/v1/auth/login", json={"username": "missing", "password": "irrelevant"}).status_code == 401
        limited = remote.post("/api/v1/auth/login", json={"username": "missing", "password": "irrelevant"})
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0


def test_validation_errors_do_not_echo_credential(client, monkeypatch):
    bootstrap = secrets.token_hex(32)
    monkeypatch.setattr(settings.collector, "enrollment_token", bootstrap)
    short = secrets.token_hex(6)
    response = client.post("/api/v1/collectors/register", json={"name": "test", "auth_token": short}, headers=bearer(bootstrap))
    assert response.status_code == 422
    assert short not in response.text

def test_snmp_configuration_accepts_alias_but_never_plaintext(client, admin_headers):
    device = client.post("/api/v1/devices", json={"name": "snmp-test", "ip": "127.0.0.1", "initial_check_types": []}, headers=admin_headers).json()
    payload = {"device_id": device["id"], "name": "Test OID", "check_type": "snmp", "target": "127.0.0.1"}
    community = secrets.token_hex(18)
    refused = client.post("/api/v1/checks", json={**payload, "params": {"credential": "lab", "community": community}}, headers=admin_headers)
    assert refused.status_code == 422
    assert community not in refused.text
    accepted = client.post("/api/v1/checks", json={**payload, "params": {"credential": "lab", "oid": "1.3.6.1.2.1.1.3.0"}}, headers=admin_headers)
    assert accepted.status_code == 201

def test_ingestion_rejects_large_body_before_validation(client):
    response = client.post("/api/v1/metrics/ingest", content=b"{}",
                           headers={"Content-Length": str(4 * 1024 * 1024 + 1)})
    assert response.status_code == 413
    assert client.get("/api/v1/stream").status_code == 401
