import secrets
from zaddesh_core.models.checks import Check, CheckState
from zaddesh_core.models.infrastructure import Device
from zaddesh_core.models.metrics import MetricRaw
from zaddesh_core.config import settings
import datetime


def test_replay_after_bulk_deletion_keeps_metrics_for_remaining_devices(client, admin_headers, monkeypatch):
    token = secrets.token_hex(32)
    monkeypatch.setattr(settings.collector, "shared_secret", token)
    headers = {"Authorization": f"Bearer {token}"}
    ids = [client.post("/api/v1/devices", headers=admin_headers, json={"name": f"Device {n}",
           "ip": f"192.0.2.{n}", "initial_check_types": ["icmp"]}).json()["id"] for n in (1, 2)]
    assert client.post("/api/v1/devices/bulk-delete", headers=admin_headers,
                       json={"device_ids": ids[:1]}).json()["deleted"] == 1
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    points = [{"device_id": device_id, "metric_key": "icmp.rtt_avg", "value": 3, "timestamp": timestamp}
              for device_id in ids]
    response = client.post("/api/v1/metrics/ingest", headers=headers, json={"collector_id": 1, "points": points})
    assert response.status_code == 202
    assert response.json() == {"ingested": 1, "duplicates": 0, "discarded": 1, "status": "success"}


def test_probe_latency_includes_all_devices_and_omits_failed_latency(client, admin_headers, db_session):
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(seconds=1)
    devices = [Device(name=f"latency-target-{index}", ip=f"192.0.2.{index}") for index in range(1, 10)]
    db_session.add_all(devices)
    db_session.flush()
    for index, device in enumerate(devices):
        check = Check(device_id=device.id, name=f"HTTP {device.name}", check_type="http", target=device.ip)
        db_session.add(check)
        db_session.flush()
        failed = index == len(devices) - 1
        db_session.add(CheckState(check_id=check.id, current_status="critical" if failed else "ok"))
        db_session.add_all([
            MetricRaw(device_id=device.id, check_id=check.id, metric_key="http.available", timestamp=now, value=0 if failed else 1),
            MetricRaw(device_id=device.id, check_id=check.id, metric_key="http.response_time_ms", timestamp=now, value=10 + index),
        ])
    db_session.commit()

    endpoint = "/api/v1/metrics/probe-latency?protocol=http&minutes=15"
    response = client.get(endpoint, headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert {item["id"] for item in data["devices"]} >= {device.id for device in devices}
    channels = {channel["device_id"]: channel for channel in data["channels"]}
    assert all(channels[device.id]["latency"][0]["value"] == 10 + index for index, device in enumerate(devices[:-1]))
    failed_channel = channels[devices[-1].id]
    assert failed_channel["probe_down"]
    assert len(failed_channel["failures"]) == 1
    assert failed_channel["latency"] == []
    filtered = client.get(f"{endpoint}&device_id={devices[-1].id}", headers=admin_headers)
    assert filtered.status_code == 200
    assert filtered.json()["channels"] == [failed_channel]


def test_metric_ingest_and_query(client, admin_headers, monkeypatch):
    # 1. Create a device
    dev_res = client.post(
        "/api/v1/devices",
        json={
            "name": "metric-target-01",
            "ip": "10.0.100.5",
            "initial_check_types": ["icmp"],
        },
        headers=admin_headers,
    )
    assert dev_res.status_code == 201
    dev_id = dev_res.json()["id"]

    # Get the auto-created check id
    chk_res = client.get(f"/api/v1/checks?device_id={dev_id}", headers=admin_headers)
    assert chk_res.status_code == 200
    chk_id = chk_res.json()[0]["id"]

    # 2. Ingest metrics using collector secret authorization
    now = datetime.datetime.utcnow()
    token = secrets.token_hex(32)
    monkeypatch.setattr(settings.collector, "shared_secret", token)
    collector_headers = {"Authorization": f"Bearer {token}"}

    batch = {
        "points": [
            {
                "device_id": dev_id,
                "check_id": chk_id,
                "metric_key": "icmp.availability",
                "timestamp": now.isoformat(),
                "value": 1.0,
                "tags": {"unit": "ratio"},
            },
            {
                "device_id": dev_id,
                "check_id": chk_id,
                "metric_key": "icmp.rtt_avg",
                "timestamp": now.isoformat(),
                "value": 4.25,
                "tags": {"unit": "ms"},
            },
        ]
    }

    ingest_res = client.post("/api/v1/metrics/ingest", json=batch, headers=collector_headers)
    assert ingest_res.status_code == 202
    assert ingest_res.json()["ingested"] == 2

    duplicate = client.post("/api/v1/metrics/ingest", json=batch, headers=collector_headers)
    assert duplicate.status_code == 202
    assert duplicate.json()["ingested"] == 0
    assert duplicate.json()["duplicates"] == 2

    # 3. Query the ingested metrics
    start_time = now - datetime.timedelta(minutes=5)
    end_time = now + datetime.timedelta(minutes=5)

    query_res = client.post(
        "/api/v1/metrics/query",
        json={
            "device_id": dev_id,
            "metric_keys": ["icmp.rtt_avg", "icmp.availability"],
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
        },
        headers=admin_headers,
    )
    assert query_res.status_code == 200
    query_data = query_res.json()
    assert len(query_data["series"]) == 2
    rtt_series = next(s for s in query_data["series"] if s["metric_key"] == "icmp.rtt_avg")
    assert len(rtt_series["points"]) == 1
    assert rtt_series["points"][0]["value"] == 4.25

    # Cross the SQLite insert chunk boundary with a duplicate in a later chunk.
    original = batch["points"][1]
    extra = [{**original, "timestamp": (now + datetime.timedelta(milliseconds=i)).isoformat()}
             for i in range(1, 251)]
    response = client.post("/api/v1/metrics/ingest", headers=collector_headers,
                           json={"points": extra + [extra[0], original]})
    assert response.status_code == 202
    assert response.json() == {"ingested": 250, "duplicates": 2, "status": "success"}


def test_availability_uses_assertion_and_old_samples_cannot_reopen_incident(client, admin_headers, monkeypatch):
    token = secrets.token_hex(32)
    monkeypatch.setattr(settings.collector, "shared_secret", token)
    collector = {"Authorization": f"Bearer {token}"}
    device_id = client.post("/api/v1/devices", json={"name": "HTTP target", "ip": "127.0.0.1", "initial_check_types": ["http"]}, headers=admin_headers).json()["id"]
    check_id = client.get(f"/api/v1/checks?device_id={device_id}", headers=admin_headers).json()[0]["id"]
    now = datetime.datetime.now(datetime.timezone.utc)

    def sample(value, offset):
        points = [{"device_id": device_id, "check_id": check_id, "metric_key": key,
                   "timestamp": (now + datetime.timedelta(seconds=offset)).isoformat(), "value": v}
                  for key, v in (("http.status_code", 200), ("http.available", value))]
        assert client.post("/api/v1/metrics/ingest", json={"points": points}, headers=collector).status_code == 202

    def status():
        return client.get(f"/api/v1/devices/{device_id}", headers=admin_headers).json()["status"]

    def alerts():
        return [alert for alert in client.get("/api/v1/alerts", headers=admin_headers).json() if alert["check_id"] == check_id]

    sample(0, 1)
    assert status() == "down"
    assert [alert["status"] for alert in alerts()] == ["active"]
    sample(0, 2)
    assert len(alerts()) == 1
    sample(1, 3)
    assert status() == "up"
    assert [alert["status"] for alert in alerts()] == ["resolved"]
    sample(0, 2)
    assert status() == "up"
    assert [alert["status"] for alert in alerts()] == ["resolved"]
