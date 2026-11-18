"""Persistence regressions run against a real, isolated MongoDB replica set."""
from datetime import datetime, timedelta, timezone
import os
from secrets import token_hex

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select, update

from zaddesh_core.api.v1 import api_v1_router
from zaddesh_core.config import settings
from zaddesh_core.database import Base
from zaddesh_core.models import Check, CheckState, Device, Role, Site
from zaddesh_core.mongo_store import MongoSession, WriteConflict, ensure_schema, mongo_database

pytestmark = pytest.mark.skipif(not os.environ.get("ZADDESH_MONGO_TEST_URI"), reason="Requires a dedicated MongoDB validation replica set")


@pytest.fixture
def mongo(monkeypatch):
    assert "zaddesh_validation" in settings.database.url, "Refusing to clear a non-validation MongoDB database"
    ensure_schema()
    database = mongo_database()
    for table in Base.metadata.sorted_tables:
        database[table.name].delete_many({})
    for name in ("entries", "source_cursors", "source_status", "_migration"):
        database[name].delete_many({})
    database["_counters"].delete_many({})
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    yield database
    for table in reversed(Base.metadata.sorted_tables):
        database[table.name].delete_many({})


def test_native_crud_relationships_microseconds_and_grouping(mongo):
    exact = datetime(2026, 11, 17, 1, 2, 3, 123456)
    with MongoSession() as db:
        site = Site(name="Validation", slug="validation")
        db.add(site)
        db.flush()
        device = Device(name="SNMP workstation", ip="192.0.2.31", site_id=site.id, is_priority=False, created_at=exact)
        db.add(device)
        db.flush()
        check = Check(device_id=device.id, name="SNMP", check_type="snmp", target=device.ip, params={"version": "2c", "credential": "default"})
        db.add(check)
        db.commit()
        identifier = device.id
    with MongoSession() as db:
        device = db.get(Device, identifier)
        assert device.created_at == exact
        assert device.site.name == "Validation"
        assert len(device.checks) == 1
        assert db.query(Device).filter(Device.name.icontains("WORK", autoescape=True)).count() == 1
        assert db.query(Device.status, func.count(Device.id)).group_by(Device.status).all() == [("unknown", 1)]
        assert db.query(Device).filter(Device.id.in_(db.query(Check.device_id).filter(Check.check_type == "snmp"))).count() == 1
        device.is_priority = True
        db.commit()
    assert mongo.devices.find_one({"id": identifier})["is_priority"] is True
    assert mongo.devices.find_one({"id": identifier})["created_at"] % 1_000_000 == 123456
    with MongoSession() as db:
        assert db.query(Device).filter(Device.id == identifier).delete() == 1
        db.commit()
    assert mongo.checks.count_documents({}) == 0


def test_transactions_rollback_uniqueness_and_concurrent_updates(mongo):
    with MongoSession() as db:
        role = Role(name="validation", permissions=[])
        db.add(role)
        db.flush()
        assert mongo.roles.count_documents({}) == 0
        db.rollback()
    assert mongo.roles.count_documents({}) == 0
    with MongoSession() as db:
        device = Device(name="Before", ip="192.0.2.40")
        db.add(device)
        db.commit()
        identifier = device.id
    with MongoSession() as first, MongoSession() as second:
        a, b = first.get(Device, identifier), second.get(Device, identifier)
        a.name = "First"
        first.commit()
        b.name = "Second"
        with pytest.raises(WriteConflict):
            second.commit()
        second.rollback()
    assert mongo.devices.find_one({"id": identifier})["name"] == "First"


def test_native_compare_and_set_and_scalar_queries(mongo):
    with MongoSession() as db:
        db.add(Device(name="Before", ip="192.0.2.45", is_priority=True))
        db.commit()
        device = db.scalar(select(Device).where(Device.ip == "192.0.2.45"))
        assert device.name == "Before"
        assert db.execute(update(Device).where(Device.id == device.id, Device.name == "Before").values(name="After")).rowcount == 1
        assert device.name == "After"
        assert db.execute(update(Device).where(Device.id == device.id, Device.name == "Before").values(name="Wrong")).rowcount == 0
        db.commit()
        assert db.scalar(select(func.count()).select_from(Device)) == 1
        assert db.execute(delete(Device).where(Device.id == device.id)).rowcount == 1
        db.commit()


def test_fresh_api_setup_snmp_inventory_ingestion_and_priority(mongo, monkeypatch):
    app = FastAPI()
    app.include_router(api_v1_router)
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    collector = token_hex(24)
    monkeypatch.setattr(settings.collector, "shared_secret", collector)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 40000)) as client:
        assert client.get("/api/v1/auth/status").json()["setup_required"]
        setup = client.post("/api/v1/auth/setup", json={"username": "mongo-admin", "email": "mongo@example.com", "password": token_hex(24)})
        assert setup.status_code == 201, setup.text
        headers = {"Authorization": "Bearer " + setup.json()["access_token"]}
        device = client.post("/api/v1/devices", headers=headers, json={"name": "SNMP workstation", "ip": "192.0.2.50", "initial_check_types": [],
            "initial_snmp": {"params": {"version": "2c", "credential": "default"}}})
        assert device.status_code == 201, device.text
        device = device.json()
        assert not device["is_priority"]
        identifier = device["id"]
        assert len(client.get("/api/v1/devices?snmp_only=true", headers=headers).json()) == 1
        checks = client.get("/api/v1/checks", headers=headers, params={"device_id": identifier}).json()
        assert len(checks) == 1
        check_id = checks[0]["id"]
        timestamp = datetime.now(timezone.utc)

        def sample(value, at):
            return client.post("/api/v1/metrics/ingest", headers={"Authorization": "Bearer " + collector}, json={"points": [
                {"device_id": identifier, "check_id": check_id, "metric_key": "snmp.available", "timestamp": at.isoformat(), "value": value},
            ]})

        first = sample(0, timestamp)
        assert first.status_code == 202, first.text
        assert first.json()["ingested"] == 1
        assert sample(0, timestamp).json()["duplicates"] == 1
        alerts = client.get("/api/v1/incidents/alerts", headers=headers)
        assert alerts.status_code == 200, alerts.text
        assert alerts.json()["alerts"] == []
        own_logs = client.get("/api/v1/logs", headers=headers, params={"origin": "zaddesh", "device_id": identifier})
        assert own_logs.status_code == 200, own_logs.text
        assert own_logs.json()
        assert client.put(f"/api/v1/devices/{identifier}", headers=headers, json={"is_priority": True}).status_code == 200
        assert sample(1, timestamp + timedelta(seconds=60)).status_code == 202
        assert sample(0, timestamp + timedelta(seconds=120)).status_code == 202
        alerts = client.get("/api/v1/incidents/alerts", headers=headers)
        assert alerts.status_code == 200, alerts.text
        assert alerts.json()["alerts"]
        latest = client.get("/api/v1/metrics/latest", headers=headers, params={"device_id": identifier})
        assert latest.status_code == 200, latest.text
        assert latest.json()
        detail = client.get(f"/api/v1/devices/{identifier}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["check_count"] == 1
        assert client.get("/api/v1/readyz").status_code == 200


@pytest.fixture
def mongo_api(mongo, monkeypatch):
    app = FastAPI()
    app.include_router(api_v1_router)
    monkeypatch.setattr(settings.features, "incident_workspace", True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 40001)) as client:
        setup = client.post("/api/v1/auth/setup", json={"username": "native-admin", "email": "native@example.com", "password": token_hex(24)})
        assert setup.status_code == 201, setup.text
        yield client, {"Authorization": "Bearer " + setup.json()["access_token"]}


def test_native_latency_window_and_correlated_failure_filter(mongo_api):
    client, headers = mongo_api
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with MongoSession() as db:
        device = Device(name="Priority probe", ip="192.0.2.70", is_priority=True)
        quiet = Device(name="Quiet workstation", ip="192.0.2.71", is_priority=False)
        db.add_all([device, quiet])
        db.flush()
        check = Check(device_id=device.id, name="ICMP", target=device.ip, check_type="icmp", params={})
        db.add(check)
        db.flush()
        db.add(CheckState(check_id=check.id, current_status="ok"))
        records = []
        for seconds, available, latency in ((120, 1, 3.2), (60, 0, 999), (30, 1, 4.5)):
            at = now - timedelta(seconds=seconds)
            for key, value in (("icmp.availability", available), ("icmp.rtt_avg", latency)):
                records.append({"device_id": device.id, "check_id": check.id, "metric_key": key, "value": value,
                                "timestamp": at, "tags": {}, "fingerprint": token_hex(32)})
        assert db.insert_metrics(records) == 6
        db.commit()
        identifier = device.id
    response = client.get("/api/v1/metrics/probe-latency", headers=headers, params={"protocol": "icmp", "minutes": 15, "priority_only": "true"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["devices"]) == 1
    assert body["devices"][0]["id"] == identifier
    assert [point["value"] for point in body["channels"][0]["latency"]] == [3.2, 4.5]
    assert len(body["channels"][0]["failures"]) == 1


def test_native_workstation_snapshot_leases_services_logs_and_priority(mongo_api):
    from zaddesh_core.api.v1.workstations import SERVICE_LINES
    from zaddesh_core.models import Collector
    from zaddesh_core.security import hash_token

    client, headers = mongo_api
    token = token_hex(24)
    with MongoSession() as db:
        collector = Collector(name="Validation inventory", auth_token_hash=hash_token(token), status="online")
        db.add(collector)
        db.commit()
        collector_id = collector.id
    worker = {"Authorization": "Bearer " + token}
    response = client.post("/api/v1/devices", headers=headers, json={"name": "On-demand workstation", "ip": "192.0.2.80", "collection_mode": "on_demand", "initial_check_types": [],
        "initial_snmp": {"params": {"version": "2c", "credential": "default"}}})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    assert client.get(f"/api/v1/collectors/{collector_id}/tasks", headers=worker).json()["tasks"] == []
    root = f"/api/v1/devices/{identifier}/inventory"
    assert client.post(root + "/refresh", headers=headers).status_code == 202
    job = client.post("/api/v1/collectors/inventory-next", headers=worker)
    assert job.status_code == 200, job.text
    assert job.headers["cache-control"] == "no-store"
    payload = {"lease": job.json()["lease"], "values": {
        "1.3.6.1.2.1.1.5.0": "Validation PC", "1.3.6.1.2.1.25.4.2.1.2.123": "sample.exe",
        "1.3.6.1.2.1.25.4.2.1.4.123": "C:/Sample/sample.exe", "1.3.6.1.2.1.25.4.2.1.7.123": "1",
        "1.3.6.1.2.1.25.3.3.1.2.1": "35", SERVICE_LINES + "1": "SampleService\tRunning",
    }}
    result_path = f"/api/v1/collectors/inventory-result/{identifier}"
    result = client.post(result_path, headers=worker, json=payload)
    assert result.status_code == 200, result.text
    assert client.post(result_path, headers=worker, json=payload).status_code == 409
    data = client.get(root, headers=headers).json()
    assert data["processes"][0]["pid"] == 123
    assert data["services"] == [{"name": "SampleService", "status": "Running"}]
    assert data["health"]["cpu_load_percent"] == 35
    assert client.get("/api/v1/incidents/alerts", headers=headers).json()["alerts"] == []


    logs = client.get("/api/v1/logs", headers=headers, params={"origin": "zaddesh", "device_id": identifier})
    assert logs.status_code == 200, logs.text
    assert logs.json()[0]["provider"] == "zaddesh"
    assert client.put(f"/api/v1/devices/{identifier}", headers=headers, json={"is_priority": True, "collection_mode": "scheduled"}).status_code == 200
    assert len(client.get(f"/api/v1/collectors/{collector_id}/tasks", headers=worker).json()["tasks"]) == 1
    assert client.post(root + "/refresh", headers=headers).status_code == 202
    job = client.post("/api/v1/collectors/inventory-next", headers=worker).json()
    result = client.post(result_path, headers=worker, json={"lease": job["lease"], "error": "SNMP validation target is offline"})
    assert result.status_code == 200, result.text
    alerts = client.get("/api/v1/incidents/alerts?category=critical", headers=headers)
    assert alerts.status_code == 200, alerts.text
    assert len(alerts.json()["alerts"]) == 1
    assert client.put(f"/api/v1/devices/{identifier}", headers=headers, json={"is_priority": False}).status_code == 200
    assert client.get("/api/v1/incidents/alerts", headers=headers).json()["alerts"] == []


def test_native_integrated_logs_are_separate_and_ignore_alert_priority(mongo_api, monkeypatch):
    from contextlib import contextmanager
    from zaddesh_core import integration_logs
    from zaddesh_core.models import ZabbixConnection
    from zaddesh_core.security import encrypt_secret

    client, headers = mongo_api
    token = token_hex(24)
    now = datetime.now(timezone.utc)
    with MongoSession() as db:
        quiet = Device(name="Quiet PC", ip="192.0.2.90", is_priority=False)
        db.add(quiet)
        db.add(ZabbixConnection(id=1, api_url="https://validation.example/api_jsonrpc.php", encrypted_token=encrypt_secret(token), version="7.0.0"))
        db.commit()
        identifier = quiet.id

    class API:
        def __init__(self):
            self.token = token

        def rows(self, method, params):
            if method == "host.get":
                return [{"hostid": "9", "name": "Quiet PC", "interfaces": [{"ip": "192.0.2.90", "useip": "1", "main": "1"}]}]
            if params.get("eventid_from"):
                return []
            return [{"eventid": "900", "clock": str(int(now.timestamp())), "name": "Application service stopped " + token,
                     "value": "1", "severity": "3", "hosts": [{"hostid": "9", "name": "Quiet PC"}]}]

    @contextmanager
    def saved(_):
        yield API()

    monkeypatch.setattr(integration_logs, "saved_api", saved)
    integration_logs.archive_once()
    integration_logs.archive_once()
    entries = client.get("/api/v1/logs", headers=headers, params={"origin": "integration", "device_id": identifier})
    assert entries.status_code == 200, entries.text
    assert len(entries.json()) == 1
    assert entries.json()[0]["provider"] == "zabbix"
    assert entries.json()[0]["origin"] == "integration"
    assert token not in entries.text
    assert client.get("/api/v1/logs", headers=headers, params={"origin": "zaddesh", "device_id": identifier}).json() == []
    status = client.get("/api/v1/logs/status", headers=headers)
    assert status.json()["zabbix"]["status"] == "ready"


def test_native_client_issue_log_chunks_and_dedup(mongo):
    import base64
    from zaddesh_core.models import Event
    from zaddesh_core.workstation_logs import EVENT_LINES, save_client_logs
    at = datetime.now(timezone.utc).isoformat()
    def encode(value):
        return base64.b64encode(value.encode()).decode()
    content = encode("Storage warning\nVolume C: has low free space")
    values = {EVENT_LINES + "1": "status|ready"}
    for part, chunk in enumerate((content[:20], content[20:])):
        values[EVENT_LINES + str(part + 2)] = f"{encode('validation-record')}|{at}|warning|{part}|2|{chunk}"
    with MongoSession() as db:
        device = Device(name="Client log validation", ip="192.0.2.48", is_priority=False)
        db.add(device)
        db.flush()
        check = Check(name="SNMP", device_id=device.id, check_type="snmp", target=device.ip, params={})
        db.add(check)
        db.flush()
        assert save_client_logs(db, device.id, check.id, values)["client_logs_added"] == 1
        db.commit()
        assert save_client_logs(db, device.id, check.id, values)["client_logs_added"] == 0
        assert db.query(Event).filter(Event.event_type == "client_issue").one().message.endswith("low free space")
        broken = dict(values)
        del broken[EVENT_LINES + "3"]
        assert "collection limit" in save_client_logs(db, device.id, check.id, broken)["client_logs_error"]

def test_verified_sql_migration_and_final_catch_up(mongo, tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from zaddesh_core.models.metrics import MetricRaw

    source = tmp_path / "migration.db"
    sql = create_engine("sqlite:///" + source.as_posix())
    Base.metadata.create_all(sql)
    with sql.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0018_client_issue_logs')"))
    with Session(sql) as db:
        device = Device(name="Before catch-up", ip="192.0.2.91", is_priority=False)
        db.add(device)
        db.flush()
        identifier = device.id
        db.add(MetricRaw(device_id=identifier, metric_key="validation", value=1, timestamp=datetime(2026, 11, 18, 0, 0, 0, 123456)))
        db.commit()
    path = Path(__file__).resolve().parents[2] / "scripts/migrate_to_mongodb.py"
    spec = importlib.util.spec_from_file_location("migration_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("ZADDESH_MIGRATION_MONGO_URI", settings.database.url)
    module.migrate(source, tmp_path / "initial.json", defer_metric_indexes=True)
    with Session(sql) as db:
        db.get(Device, identifier).name = "After catch-up"
        db.add(MetricRaw(device_id=identifier, metric_key="validation", value=2))
        db.commit()
    module.migrate(source, tmp_path / "final.json", catch_up=True)
    assert mongo.devices.find_one({"id": identifier})["name"] == "After catch-up"
    assert mongo.metrics_raw.count_documents({}) == 2
    assert mongo.metrics_raw.find_one({"value": 1})["timestamp"] % 1_000_000 == 123456
    assert mongo["_migration"].find_one({"_id": "sql"})["state"] == "verified"

def test_native_population_and_write_locks_preserve_account_rechecks(mongo):
    with MongoSession() as db:
        role = Role(name="locked-original", permissions=[])
        db.add(role)
        db.commit()
        identifier = role.id
    first, second = MongoSession(), MongoSession()
    try:
        assert first.get(Role, identifier).name == "locked-original"
        with MongoSession() as changed:
            changed.get(Role, identifier).name = "locked-current"
            changed.commit()
        refreshed = first.query(Role).filter_by(id=identifier).populate_existing().one()
        assert refreshed.name == "locked-current"
        first.query(Role).filter_by(id=identifier).with_for_update().one()
        with pytest.raises(WriteConflict):
            second.query(Role).filter_by(id=identifier).with_for_update().one()
        second.rollback()
        first.commit()
        assert second.query(Role).filter_by(id=identifier).with_for_update().one().name == "locked-current"
        second.commit()
    finally:
        first.close()
        second.close()

def test_unclaimed_inventory_times_out_without_a_target_down_alert(mongo_api):
    from zaddesh_core.models import WorkstationInventory
    client, headers = mongo_api
    created = client.post('/api/v1/devices', headers=headers, json={
        'name': 'Waiting for collector', 'ip': '192.0.2.92', 'is_priority': True,
        'collection_mode': 'on_demand', 'initial_check_types': [],
        'initial_snmp': {'params': {'version': '3', 'credential': 'default'}},
    })
    assert created.status_code == 201
    identifier = created.json()['id']
    path = f'/api/v1/devices/{identifier}/inventory'
    assert client.post(path + '/refresh', headers=headers).status_code == 202
    with MongoSession() as db:
        db.get(WorkstationInventory, identifier).requested_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=3)
        db.commit()
    snapshot = client.get(path, headers=headers).json()
    assert snapshot['status'] == 'failed'
    assert 'collector' in snapshot['message']
    assert client.get(f'/api/v1/devices/{identifier}', headers=headers).json()['status'] == 'unknown'
    assert client.get('/api/v1/incidents/alerts', headers=headers).json()['alerts'] == []
    assert client.post(path + '/refresh', headers=headers).json()['status'] == 'queued'

def test_native_ingestion_uses_the_retry_fingerprint_index(mongo):
    from pymongo import MongoClient, monitoring

    class Writes(monitoring.CommandListener):
        def __init__(self):
            self.filters = []

        def started(self, event):
            if event.command_name == 'update' and event.command.get('update') == 'metrics_raw':
                self.filters.extend(item['q'] for item in event.command['updates'])

        def succeeded(self, event):
            pass

        def failed(self, event):
            pass

    listener = Writes()
    with MongoClient(settings.database.url, event_listeners=[listener]) as connection:
        database = connection.get_default_database()
        with MongoSession(database=database, initialize=False) as db:
            records = [{'fingerprint': 'a' * 64, 'device_id': 1, 'check_id': None, 'metric_key': 'validation',
                        'timestamp': datetime.now(timezone.utc), 'value': 1, 'tags': {}}]
            assert db.insert_metrics(records) == 1
            db.commit()
            assert db.insert_metrics(records) == 0
            db.commit()
        assert listener.filters
        explained = database.command('explain', {'find': 'metrics_raw', 'filter': listener.filters[0]}, verbosity='queryPlanner')
        def indexed(value):
            if isinstance(value, dict):
                return value.get('indexName') == 'idx_fingerprint' or any(indexed(item) for item in value.values())
            if isinstance(value, list):
                return any(indexed(item) for item in value)
            return False
        assert indexed(explained['queryPlanner']['winningPlan'])

def test_native_state_lock_and_update_batches_bound_database_round_trips(mongo):
    from pymongo import MongoClient, monitoring
    with MongoSession() as db:
        db.add_all([Role(name=f'batch-{index}', permissions=[]) for index in range(25)])
        db.commit()

    class Commands(monitoring.CommandListener):
        def __init__(self):
            self.updates = 0

        def started(self, event):
            if event.command_name == 'update' and event.command.get('update') == 'roles':
                self.updates += 1

        def succeeded(self, event):
            pass

        def failed(self, event):
            pass

    commands = Commands()
    with MongoClient(settings.database.url, event_listeners=[commands]) as connection:
        with MongoSession(database=connection.get_default_database(), initialize=False) as db:
            rows = db.query(Role).order_by(Role.id).with_for_update().all()
            for row in rows:
                row.description = 'Updated in one version-checked batch'
            db.commit()
    assert commands.updates == 2
    assert mongo.roles.count_documents({'description': 'Updated in one version-checked batch'}) == 25

def test_native_concurrent_ingestion_is_retryable_and_deduplicated(mongo_api):
    from concurrent.futures import ThreadPoolExecutor
    from zaddesh_core.models import Collector
    from zaddesh_core.security import hash_token
    client, headers = mongo_api
    created = client.post('/api/v1/devices', headers=headers, json={'name': 'Concurrent ingest', 'ip': '192.0.2.93', 'initial_check_types': []})
    assert created.status_code == 201
    identifier = created.json()['id']
    token = token_hex(24)
    with MongoSession() as db:
        collector = Collector(name='Concurrent ingest', auth_token_hash=hash_token(token))
        db.add(collector)
        db.commit()
        collector_id = collector.id
    payload = {'collector_id': collector_id, 'points': [{'device_id': identifier, 'metric_key': 'validation',
               'timestamp': datetime.now(timezone.utc).isoformat(), 'value': 1, 'tags': {}}]}
    def send():
        return client.post('/api/v1/metrics/ingest', headers={'Authorization': 'Bearer ' + token}, json=payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(), range(2)))
    assert all(response.status_code in {202, 409} for response in responses)
    assert any(response.status_code == 202 for response in responses)
    assert send().status_code == 202
    assert mongo_database().metrics_raw.count_documents({'device_id': identifier}) == 1

def test_imported_availability_noise_is_hidden_but_resource_alerts_remain(mongo_api):
    from zaddesh_core.models import Alert
    from zaddesh_core.models.zabbix import ZabbixHostImport
    client, headers = mongo_api
    with MongoSession() as db:
        device=Device(name='Imported priority host', ip='192.0.2.94', is_priority=True, status='down')
        db.add(device)
        db.flush()
        db.add(ZabbixHostImport(source_key='validation', host_id='94', device_id=device.id))
        db.add(Alert(device_id=device.id, title='Availability: SNMP counter', severity='critical'))
        db.add(Alert(device_id=device.id, title='High CPU utilization', severity='critical'))
        db.commit()
    result=client.get('/api/v1/incidents/alerts?category=critical&view=current&include_count=true', headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()['total'] == 1
    assert result.json()['alerts'][0]['category'] == 'cpu_usage'
