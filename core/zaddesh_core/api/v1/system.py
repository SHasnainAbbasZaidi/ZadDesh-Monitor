from __future__ import annotations

import time
import datetime
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from sqlalchemy import text

from zaddesh_core.database import get_db
from zaddesh_core.mongo_store import MongoSession
from zaddesh_core.models.infrastructure import Device, Collector
from zaddesh_core.models.checks import Check
from zaddesh_core.models.alerts import Alert
from zaddesh_core.models.auth import User
from zaddesh_core.schemas.system import SystemInfo
from zaddesh_core.config import settings

router = APIRouter(prefix="", tags=["System & Health"])

_START_TIME = time.time()


@router.get("/healthz")
def health_check():
    """Liveness probe: verifies process is running."""
    return {"status": "ok", "timestamp": datetime.datetime.utcnow().isoformat()}


@router.get("/readyz")
def readiness_check(db: Session = Depends(get_db)):
    """Readiness probe: verifies database connectivity."""
    try:
        if isinstance(db, MongoSession):
            db.ping()
        else:
            db.execute(text("SELECT fingerprint FROM metrics_raw LIMIT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception:
        raise HTTPException(503, "Database unavailable") from None


@router.get("/system/info", response_model=SystemInfo)
def system_info(db: Session = Depends(get_db)):
    """Get system health and inventory statistics."""
    user_count = db.query(User).count()
    device_count = db.query(Device).count()
    check_count = db.query(Check).count()
    alert_count = db.query(Alert).filter(Alert.status == "active").count()
    collector_count = db.query(Collector).count()

    db_type = db.get_bind().dialect.name

    return SystemInfo(
        version="0.1.0",
        status="healthy",
        environment="demo" if settings.demo_mode else "live",
        is_setup_required=user_count == 0,
        database_type=db_type,
        uptime_seconds=round(time.time() - _START_TIME, 2),
        counts={
            "users": user_count,
            "devices": device_count,
            "checks": check_count,
            "active_alerts": alert_count,
            "collectors": collector_count,
        },
    )


@router.get("/metrics")
def prometheus_metrics(db: Session = Depends(get_db)):
    """Prometheus-compatible scrape endpoint."""
    device_up = db.query(Device).filter(Device.status == "up").count()
    device_down = db.query(Device).filter(Device.status == "down").count()
    device_total = db.query(Device).count()
    alerts_active = db.query(Alert).filter(Alert.status == "active").count()
    uptime = time.time() - _START_TIME

    lines = [
        "# HELP zaddesh_uptime_seconds Process uptime in seconds.",
        "# TYPE zaddesh_uptime_seconds counter",
        f"zaddesh_uptime_seconds {uptime:.2f}",
        "# HELP zaddesh_devices_total Total monitored devices.",
        "# TYPE zaddesh_devices_total gauge",
        f"zaddesh_devices_total {device_total}",
        "# HELP zaddesh_devices_up Monitored devices in UP state.",
        "# TYPE zaddesh_devices_up gauge",
        f"zaddesh_devices_up {device_up}",
        "# HELP zaddesh_devices_down Monitored devices in DOWN state.",
        "# TYPE zaddesh_devices_down gauge",
        f"zaddesh_devices_down {device_down}",
        "# HELP zaddesh_alerts_active Current active alerts.",
        "# TYPE zaddesh_alerts_active gauge",
        f"zaddesh_alerts_active {alerts_active}",
    ]
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

