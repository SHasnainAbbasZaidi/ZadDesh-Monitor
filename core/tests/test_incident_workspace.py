from datetime import datetime, timedelta, timezone
from secrets import token_hex

from zaddesh_core.config import settings
from zaddesh_core.incidents import redact


def test_incident_lifecycle_including_buffered_flaps(client, admin_headers, viewer_headers, monkeypatch):
    assert client.get("/api/v1/incidents/alerts", headers=admin_headers).status_code == 404
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    token = token_hex(32)
    monkeypatch.setattr(settings.collector, "shared_secret", token)
    created = client.post("/api/v1/devices", headers=admin_headers, json={
        "name": "Incident test", "ip": "192.0.2.4", "initial_check_types": ["http"],
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
