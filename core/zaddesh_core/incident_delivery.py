from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import cast
from urllib.parse import urlsplit

import httpx
from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from pymongo.errors import PyMongoError

from zaddesh_core.config import settings
from zaddesh_core.database import SessionLocal
from zaddesh_core.incidents import utc_now
from zaddesh_core.models.incidents import IncidentDelivery
from zaddesh_core.models import Alert, Device

logger = logging.getLogger(__name__)


def deliver_once() -> None:
    target = settings.features.incident_webhook_url
    if not target or settings.demo_mode:
        return
    destination = urlsplit(target)
    if (destination.username or destination.password or not destination.hostname
        or (destination.scheme != "https" and not (
            destination.scheme == "http" and destination.hostname in {"127.0.0.1", "::1", "localhost"}))):
        logger.error("Incident webhook requires an HTTPS or loopback URL without credentials")
        return
    with SessionLocal() as db:
        now = utc_now()
        item = db.query(IncidentDelivery).filter(IncidentDelivery.status.in_(("queued", "sending")),
                                                IncidentDelivery.next_attempt <= now).order_by(IncidentDelivery.id).first()
        if item is None:
            return
        alert = db.get(Alert, item.alert_id)
        device = db.get(Device, alert.device_id) if alert else None
        if device is None or not device.is_priority:
            item.status, item.outcome = "failed", "Device priority is disabled"
            db.commit()
            return
        claimed = cast(CursorResult, db.execute(update(IncidentDelivery).where(IncidentDelivery.id == item.id,
            IncidentDelivery.status.in_(("queued", "sending")), IncidentDelivery.next_attempt <= now).values(
                status="sending", next_attempt=now + timedelta(seconds=45),
        ))).rowcount
        if not claimed:
            return
        db.commit()
        identifier, payload = item.id, item.payload
        db.close()
        try:
            with httpx.Client(follow_redirects=False, timeout=5, trust_env=False) as client:
                response = client.post(target, json=payload, headers={"Idempotency-Key": f"zaddesh-incident-{identifier}"})
            success, outcome = response.is_success, f"HTTP {response.status_code}"
        except httpx.HTTPError:
            success, outcome = False, "Transport error"
        item = db.get(IncidentDelivery, identifier)
        if item is None:
            return
        item.attempts += 1
        item.outcome = outcome
        item.status = "delivered" if success else "failed" if item.attempts >= 5 else "queued"
        item.delivered_at = utc_now() if success else None
        item.next_attempt = utc_now() + timedelta(seconds=2 ** item.attempts)
        db.commit()
    from zaddesh_core.api.v1.stream import notify_change

    notify_change()


async def delivery_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(deliver_once)
        except (SQLAlchemyError, PyMongoError, ValueError):
            logger.error("Incident delivery worker failed to process its queue")
        try:
            await asyncio.wait_for(stop.wait(), 2)
        except asyncio.TimeoutError:
            pass
