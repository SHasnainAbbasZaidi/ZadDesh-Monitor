"""Archive Zabbix event logs independently of device alert priority."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pymongo import UpdateOne
from pymongo.errors import PyMongoError
from sqlalchemy.exc import SQLAlchemyError

from zaddesh_core.api.v1.logs import log_collection
from zaddesh_core.api.v1.zabbix import HOST_FIELDS, local_matches, saved_api, source_key, visible
from zaddesh_core.config import settings
from zaddesh_core.database import SessionLocal
from zaddesh_core.incidents import redact
from zaddesh_core.models.zabbix import ZabbixConnection

logger = logging.getLogger(__name__)


def record_status(error: str | None = None):
    with SessionLocal() as db:
        connection = db.get(ZabbixConnection, 1)
        if connection is not None:
            key = "zabbix:" + source_key(connection.api_url)
            log_collection().database.source_status.update_one({"_id": key}, {"$set": {"provider": "zabbix", "last_poll": datetime.now(timezone.utc),
                "status": "error" if error else "ready", "error": error}}, upsert=True)


def archive_once():
    with SessionLocal() as db:
        connection = db.get(ZabbixConnection, 1)
        if connection is None:
            return
        collection = log_collection()
        cursors = collection.database["source_cursors"]
        key = "zabbix:" + source_key(connection.api_url)
        cursor = cursors.find_one({"_id": key})
        now = datetime.now(timezone.utc)
        since = int((now - timedelta(hours=1)).timestamp()) if cursor is None else cursor["from_clock"]
        with saved_api(db) as api:
            params = {"source": 0, "object": 0, "time_from": since, "sortfield": "eventid", "sortorder": "ASC", "limit": 1000,
                      "output": ["eventid", "clock", "name", "value", "severity"], "selectHosts": ["hostid", "name", "host"]}
            if cursor and cursor.get("event_id"):
                params["eventid_from"] = str(int(cursor["event_id"]) + 1)
            records = api.rows("event.get", params)
            host_ids = sorted({str(host["hostid"]) for row in records for host in row.get("hosts", []) if isinstance(host, dict) and str(host.get("hostid", "")).isdecimal()})
            inventory = api.rows("host.get", {**HOST_FIELDS, "hostids": host_ids}) if host_ids else []
            matched = local_matches(db, connection.api_url, inventory)
            operations = []
            last_id = cursor.get("event_id") if cursor else None
            levels = {"0": "info", "1": "info", "2": "warning", "3": "warning", "4": "critical", "5": "critical"}
            for event in records:
                event_id, clock = str(event.get("eventid", "")), str(event.get("clock", ""))
                if not event_id.isascii() or not event_id.isdecimal() or not clock.isascii() or not clock.isdecimal() or len(clock) > 10 or len(event_id) > 20:
                    continue
                timestamp = datetime.fromtimestamp(int(clock), timezone.utc)
                hosts = [host for host in event.get("hosts", []) if isinstance(host, dict)]
                names = [visible(host.get("name") or host.get("host"), api.token) for host in hosts]
                device_ids = {identifier for host in hosts for identifier in matched.get(str(host.get("hostid")), [])}
                name = ", ".join(filter(None, names))[:255] or "Zabbix"
                message = redact(str(event.get("name") or "Zabbix event").replace(api.token, "[redacted]"))[:8100]
                state = "Recovery" if str(event.get("value")) == "0" else "Problem"
                identifier = str(uuid5(NAMESPACE_URL, key + ":" + event_id))
                document = {"timestamp": timestamp, "source": name, "provider": "zabbix", "origin": "integration", "integration_key": key,
                            "host_ids": [str(host["hostid"]) for host in hosts if host.get("hostid")],
                            "device_id": next(iter(device_ids)) if len(device_ids) == 1 else None,
                            "severity": "info" if state == "Recovery" else levels.get(str(event.get("severity")), "info"),
                            "message": message + "\n" + state + " · Zabbix event " + event_id,
                            "received_at": now, "expires_at": timestamp + timedelta(days=settings.logs.retention_days)}
                operations.append(UpdateOne({"_id": identifier}, {"$setOnInsert": document}, upsert=True))
                last_id = event_id
        if operations:
            collection.bulk_write(operations, ordered=True)
        cursors.update_one({"_id": key}, {"$set": {"from_clock": since, "event_id": last_id, "updated_at": now}}, upsert=True)
        collection.database.source_status.update_one({"_id": key}, {"$set": {"provider": "zabbix", "last_poll": now, "status": "ready", "error": None}}, upsert=True)


async def integration_log_loop(stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.to_thread(archive_once)
        except (SQLAlchemyError, PyMongoError, HTTPException, ValueError, OSError):
            logger.warning("Integrated log collection will retry")
            try:
                await asyncio.to_thread(record_status, "Zabbix event collection failed. Check API connectivity and event read permissions.")
            except (SQLAlchemyError, PyMongoError, HTTPException, ValueError, OSError):
                pass
        try:
            await asyncio.wait_for(stop.wait(), 15)
        except asyncio.TimeoutError:
            pass
