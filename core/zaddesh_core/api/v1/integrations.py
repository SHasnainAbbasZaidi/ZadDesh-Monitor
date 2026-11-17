from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import require_role
from zaddesh_core.database import get_db
from zaddesh_core.models.auth import AuditLog, User
from zaddesh_core.models.integrations import IntegrationConnection
from zaddesh_core.security import allows_secret_transport, decrypt_secret, encrypt_secret

admin = require_role(["admin"])
router = APIRouter(prefix="/integrations", tags=["API integrations"], dependencies=[Depends(admin)])
PROVIDERS: dict[str, dict[str, Any]] = {
    "grafana": {"name": "Grafana", "path": "/api/org", "auth": ["bearer", "basic"], "default_auth": "bearer",
                "hint": "Grafana base URL and a service account token with organization read access."},
    "prometheus": {"name": "Prometheus", "path": "/api/v1/query", "auth": ["none", "bearer", "basic"], "default_auth": "none",
                   "hint": "Prometheus base URL. The connection test runs the read-only count(up) query."},
    "solarwinds": {"name": "SolarWinds", "path": "/SolarWinds/InformationService/v3/Json/Query", "auth": ["basic"], "default_auth": "basic",
                   "hint": "SolarWinds server URL, usually HTTPS on port 17774. Use a SWIS account with node read access."},
    "graylog": {"name": "Graylog", "path": "/api/system", "auth": ["graylog_token", "basic"], "default_auth": "graylog_token",
                "hint": "Graylog server URL. Use an access token or an account with system read access."},
    "loggly": {"name": "Loggly", "path": "/apiv2/search", "auth": ["bearer"], "default_auth": "bearer",
               "hint": "Your account URL, such as https://account.loggly.com, and a search API token."},
    "logzio": {"name": "Logz.io", "path": "/v2/whoami", "auth": ["api_key"], "default_auth": "api_key",
               "hint": "Your region's API base URL and API token. The test reads the account identity."},
    "opentelemetry": {"name": "OpenTelemetry", "path": "/v1/logs", "auth": ["none", "bearer", "basic", "api_key"], "default_auth": "none",
                      "hint": "OTLP/HTTP receiver base URL, usually port 4318. The test sends an empty JSON log batch to validate receiver access."},
}


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_url: str = Field(min_length=1, max_length=2048)
    auth_type: Literal["none", "bearer", "basic", "api_key", "graylog_token"] = "none"
    username: str = Field(default="", max_length=256)
    header_name: str = Field(default="X-API-Key", max_length=128)
    secret: SecretStr = Field(default=SecretStr(""), max_length=4096)


def definition(provider: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown integration")
    return PROVIDERS[provider]


def normalize_url(value: str, path: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(ord(character) < 33 or character in "\\" for character in value.strip())):
            raise ValueError
        _ = parsed.port
        base = parsed.path.rstrip("/")
        # Accept a base URL or the documented API endpoint pasted from the provider.
        if base.endswith(path):
            base = base[:-len(path)]
        elif base.endswith(path.rsplit("/", 1)[0]):
            base = base[:-len(path.rsplit("/", 1)[0])]
        return urlunsplit((parsed.scheme, parsed.netloc, base, "", ""))
    except ValueError:
        raise HTTPException(422, "Enter an HTTP or HTTPS base URL without credentials, query parameters or a fragment") from None


def prepared(provider: str, payload: ConnectionInput, saved: IntegrationConnection | None) -> tuple[dict[str, str], str]:
    spec = definition(provider)
    if payload.auth_type not in spec["auth"]:
        raise HTTPException(422, "Choose an authentication method supported by this integration")
    values = {"api_url": normalize_url(payload.api_url, spec["path"]), "auth_type": payload.auth_type,
              "username": payload.username.strip() if payload.auth_type == "basic" else "",
              "header_name": ("X-API-TOKEN" if provider == "logzio" else payload.header_name.strip()) if payload.auth_type == "api_key" else ""}
    if values["auth_type"] == "basic" and (not values["username"] or ":" in values["username"]):
        raise HTTPException(422, "Enter a valid username")
    if any(ord(character) < 32 for character in values["username"]):
        raise HTTPException(422, "Enter a valid username")
    if values["auth_type"] == "api_key" and (
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", values["header_name"])
        or values["header_name"].lower() in {"host", "authorization", "content-type", "content-length", "connection", "cookie", "transfer-encoding"}
    ):
        raise HTTPException(422, "Enter a valid API key header name")
    if values["auth_type"] == "none":
        return values, ""
    secret = payload.secret.get_secret_value()
    if not secret and saved and all(getattr(saved, field) == value for field, value in values.items()):
        try:
            secret = decrypt_secret(saved.encrypted_secret)
        except (RuntimeError, ValueError, InvalidTag):
            raise HTTPException(503, "Could not decrypt the stored credential. Check the core encryption key.") from None
    if not secret or any(ord(character) < 32 or ord(character) == 127 for character in secret):
        raise HTTPException(422, "Enter a credential. Re-enter it when changing the endpoint or authentication settings.")
    if secret in values["api_url"]:
        raise HTTPException(422, "Keep credentials in the credential field")
    return values, secret


def summary(row: IntegrationConnection) -> dict[str, Any]:
    return {"provider": row.provider, "api_url": row.api_url, "auth_type": row.auth_type,
            "username": row.username, "header_name": row.header_name, "has_secret": bool(row.encrypted_secret)}


def secure_transport(request: Request) -> None:
    if not allows_secret_transport(request):
        raise HTTPException(403, "Use HTTPS or local loopback to configure API credentials")


@router.get("")
def list_connections(db: Session = Depends(get_db)):
    rows = {row.provider: row for row in db.query(IntegrationConnection).all()}
    return [{"provider": provider, **spec, "connection": summary(rows[provider]) if provider in rows else None}
            for provider, spec in PROVIDERS.items()]


@router.put("/{provider}", dependencies=[Depends(secure_transport)])
def save_connection(provider: str, payload: ConnectionInput, db: Session = Depends(get_db), user: User = Depends(admin)):
    row = db.get(IntegrationConnection, provider)
    values, secret = prepared(provider, payload, row)
    try:
        encrypted = encrypt_secret(secret)
    except RuntimeError:
        raise HTTPException(503, "Configure the core encryption key before storing API credentials") from None
    if row is None:
        row = IntegrationConnection(provider=provider)
        db.add(row)
    for field, value in values.items():
        setattr(row, field, value)
    row.encrypted_secret = encrypted
    db.add(AuditLog(user_id=user.id, action="integration.saved", entity_type="integration", entity_id=provider))
    db.commit()
    return summary(row)


@router.delete("/{provider}", status_code=204)
def remove_connection(provider: str, db: Session = Depends(get_db), user: User = Depends(admin)):
    definition(provider)
    row = db.get(IntegrationConnection, provider)
    if row:
        db.delete(row)
        db.add(AuditLog(user_id=user.id, action="integration.removed", entity_type="integration", entity_id=provider))
        db.commit()


def probe(provider: str, values: dict[str, str], secret: str) -> None:
    headers = {"Accept": "application/json"}
    auth = None
    if values["auth_type"] == "bearer":
        headers["Authorization"] = "Bearer " + secret
    elif values["auth_type"] == "api_key":
        headers[values["header_name"]] = secret
    elif values["auth_type"] == "basic":
        auth = httpx.BasicAuth(values["username"], secret)
    elif values["auth_type"] == "graylog_token":
        auth = httpx.BasicAuth(secret, "token")
    if provider == "graylog":
        headers["X-Requested-By"] = "ZadDesh"
    options: dict[str, Any] = {}
    if provider == "prometheus":
        options["params"] = {"query": "count(up)"}
    elif provider == "solarwinds":
        options["params"] = {"query": "SELECT TOP 1 NodeID FROM Orion.Nodes"}
    elif provider == "loggly":
        options["params"] = {"q": "*", "from": "-1m", "until": "now", "size": 1}
    elif provider == "opentelemetry":
        options["json"] = {"resourceLogs": []}
    method = "POST" if provider == "opentelemetry" else "GET"
    try:
        with httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False, trust_env=False, auth=auth, headers=headers) as client:
            with client.stream(method, values["api_url"] + definition(provider)["path"], **options) as response:
                if response.status_code in {401, 403}:
                    raise HTTPException(400, "The service rejected the credential or its access permissions")
                if response.is_redirect:
                    raise HTTPException(400, "The service redirected the request. Enter its final API base URL.")
                if response.status_code != 200:
                    raise HTTPException(400, f"The service returned HTTP {response.status_code}. Check the API URL and service status.")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 262144:
                        raise HTTPException(400, "The API response exceeded the connection test limit")
                result = json.loads(body)
    except httpx.TimeoutException:
        raise HTTPException(400, "Connection timed out. Check the address, port and firewall.") from None
    except (httpx.HTTPError, UnicodeError, ValueError):
        raise HTTPException(400, "Could not read the service API. Check the URL, TLS certificate and response format.") from None
    valid = isinstance(result, dict)
    if valid:
        if provider == "grafana":
            valid = "id" in result and "name" in result
        elif provider == "prometheus":
            valid = result.get("status") == "success" and isinstance(result.get("data"), dict) and "result" in result["data"]
        elif provider == "solarwinds":
            valid = isinstance(result.get("results"), list)
        elif provider == "graylog":
            valid = "version" in result and "node_id" in result
        elif provider == "loggly":
            valid = "rsid" in result
        elif provider == "logzio":
            valid = "accountId" in result and "accountName" in result
        else:
            partial = result.get("partialSuccess", result.get("partial_success", {}))
            valid = (
                not (set(result) - {"partialSuccess", "partial_success"})
                and isinstance(partial, dict)
                and partial.get("rejectedLogRecords", partial.get("rejected_log_records", 0)) in (0, "0")
                and not partial.get("errorMessage", partial.get("error_message"))
            )
    if not valid:
        raise HTTPException(400, "The endpoint did not return the expected provider API response")


@router.post("/{provider}/test", dependencies=[Depends(secure_transport)])
def test_connection(provider: str, payload: ConnectionInput, db: Session = Depends(get_db)):
    values, secret = prepared(provider, payload, db.get(IntegrationConnection, provider))
    probe(provider, values, secret)
    return {"ok": True, "message": "OTLP/HTTP receiver accepted an empty log batch" if provider == "opentelemetry" else "API access verified"}
