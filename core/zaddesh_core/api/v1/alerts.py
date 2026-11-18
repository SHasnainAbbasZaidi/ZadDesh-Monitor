from __future__ import annotations

import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from zaddesh_core.database import get_db
from zaddesh_core.models.alerts import Alert, Acknowledgement, Event
from zaddesh_core.models.auth import User
from zaddesh_core.models.infrastructure import Device
from zaddesh_core.schemas.alert import AlertRead, EventRead, AlertAckRequest
from zaddesh_core.api.deps import get_current_user, require_role

from zaddesh_core.api.v1.stream import notify_change

router = APIRouter(prefix="", tags=["Alerts & Events"])


@router.get("/alerts", response_model=List[AlertRead])
def list_alerts(
    status_filter: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = None,
    device_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Alert).join(Device, Device.id == Alert.device_id).filter(Device.is_priority.is_(True))
    if status_filter:
        q = q.filter(Alert.status == status_filter.lower())
    if severity:
        q = q.filter(Alert.severity == severity.lower())
    if device_id:
        q = q.filter(Alert.device_id == device_id)
    return q.order_by(Alert.triggered_at.desc()).offset(offset).limit(limit).all()


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertRead)
def acknowledge_alert(
    alert_id: int,
    payload: AlertAckRequest,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.status != "active":
        raise HTTPException(status_code=409, detail="Only active incidents can be acknowledged")
    alert.status = "acknowledged"
    ack = Acknowledgement(
        alert_id=alert.id,
        user_id=current_user.id,
        comment=payload.comment,
    )
    db.add(ack)

    event = Event(
        device_id=alert.device_id,
        check_id=alert.check_id,
        event_type="alert_acknowledged",
        severity="info",
        title=f"Alert Acknowledged: {alert.title}",
        message=f"{current_user.username}: {payload.comment}",
        details={"alert_id": alert.id, "user_id": current_user.id},
    )
    db.add(event)

    db.commit()
    notify_change()
    db.refresh(alert)
    return alert


@router.post("/alerts/{alert_id}/resolve", response_model=AlertRead)
def resolve_alert(
    alert_id: int,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    if alert.status == "resolved":
        return alert
    alert.status = "resolved"
    alert.resolved_at = datetime.datetime.utcnow()

    event = Event(
        device_id=alert.device_id,
        check_id=alert.check_id,
        event_type="alert_resolved",
        severity="info",
        title=f"Alert Resolved: {alert.title}",
        message=f"Resolved manually by {current_user.username}",
        details={"alert_id": alert.id, "user_id": current_user.id},
    )
    db.add(event)

    db.commit()
    notify_change()
    db.refresh(alert)
    return alert


@router.get("/events", response_model=List[EventRead])
def list_events(
    device_id: Optional[int] = None,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Event)
    if device_id is None:
        q = q.outerjoin(Device, Device.id == Event.device_id).filter(or_(Event.device_id.is_(None), Device.is_priority.is_(True)))
    if device_id:
        q = q.filter(Event.device_id == device_id)
    if event_type:
        q = q.filter(Event.event_type == event_type)
    if severity:
        q = q.filter(Event.severity == severity.lower())
    return q.order_by(Event.timestamp.desc()).offset(offset).limit(limit).all()

