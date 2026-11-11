from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from zaddesh_core.database import Base


class Incident(Base):
    __tablename__ = "alert_incidents"

    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True)
    correlation_key: Mapped[str] = mapped_column(String(128), unique=True)
    occurrence_count: Mapped[int] = mapped_column(default=0)
    flapping: Mapped[bool] = mapped_column(default=False)
    acknowledged_at: Mapped[datetime | None]
    silenced_until: Mapped[datetime | None]
    suppression: Mapped[str | None] = mapped_column(String(128))
    transitions: Mapped[list[str]] = mapped_column(JSON, default=list)


class IncidentTimeline(Base):
    __tablename__ = "alert_timeline"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class IncidentParent(Base):
    __tablename__ = "incident_parents"

    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    parent_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))


class IncidentFilter(Base):
    __tablename__ = "incident_filters"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    values: Mapped[dict[str, str]] = mapped_column(JSON)


class IncidentDelivery(Base):
    __tablename__ = "incident_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt: Mapped[datetime] = mapped_column(index=True)
    delivered_at: Mapped[datetime | None]
    outcome: Mapped[str | None] = mapped_column(String(64))
