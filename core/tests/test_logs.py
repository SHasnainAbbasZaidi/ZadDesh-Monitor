from datetime import timedelta
import os
from secrets import token_hex
from uuid import uuid4

import pytest
from pymongo import MongoClient

from zaddesh_core.api.v1.logs import log_collection
from zaddesh_core.config import settings


def test_logs_require_authentication_and_separate_ingest_token(client, admin_headers, viewer_headers, monkeypatch):
    monkeypatch.setattr(settings.logs, "mongo_uri", None)
    monkeypatch.setattr(settings.logs, "ingest_token", token_hex(32))
    assert client.get("/api/v1/logs", headers=admin_headers).status_code == 503
    assert client.get("/api/v1/logs", headers=viewer_headers).status_code == 503
    assert client.get("/api/v1/logs").status_code == 401
    assert client.post("/api/v1/logs/ingest", headers=admin_headers, json={
        "entries": [{"source": "lab", "message": "Test log"}],
    }).status_code == 401


@pytest.mark.skipif(not os.getenv("ZADDESH_TEST_MONGO_URI"), reason="requires a real MongoDB test server")
def test_mongo_ingestion_deduplicates_and_filters(client, admin_headers, viewer_headers, monkeypatch):
    uri = os.environ["ZADDESH_TEST_MONGO_URI"]
    database = "zaddesh_test_" + uuid4().hex
    token = token_hex(32)
    monkeypatch.setattr(settings.logs, "mongo_uri", uri)
    monkeypatch.setattr(settings.logs, "database", database)
    monkeypatch.setattr(settings.logs, "ingest_token", token)
    monkeypatch.setattr(settings.logs, "retention_days", 30)
    headers = {"Authorization": f"Bearer {token}"}
    entry = {"event_id": str(uuid4()), "source": "lab", "severity": "warning", "message": "Test log"}
    endpoint = "http://127.0.0.1/api/v1/logs/ingest"
    log_collection.cache_clear()
    try:
        assert client.post(endpoint, headers=headers, json={"entries": [entry]}).json() == {"received": 1, "inserted": 1}
        assert client.post(endpoint, headers=headers, json={"entries": [entry]}).json() == {"received": 1, "inserted": 0}
        matching = client.get("/api/v1/logs?source=lab&severity=warning", headers=admin_headers)
        assert matching.status_code == 200
        assert [row["event_id"] for row in matching.json()] == [entry["event_id"]]
        assert client.get("/api/v1/logs?source=lab", headers=viewer_headers).json() == matching.json()
        assert client.get("/api/v1/logs?severity=info", headers=admin_headers).json() == []
        assert client.get("/api/v1/logs", headers=headers).status_code == 401
        assert log_collection().index_information()["expires_at_1"]["expireAfterSeconds"] == 0
        stored = log_collection().find_one({"_id": entry["event_id"]})
        assert stored is not None and stored["expires_at"] - stored["received_at"] == timedelta(days=30)
    finally:
        collection = log_collection()
        collection.database.client.close()
        log_collection.cache_clear()
        with MongoClient(uri, serverSelectionTimeoutMS=2500) as mongo:
            mongo.drop_database(database)
