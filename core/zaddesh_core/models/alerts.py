from __future__ import annotations

import datetime
from typing import Any
from sqlalchemy import (
    Integer,
    Float,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from zaddesh_core.database import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metric_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    condition_type: Mapped[str] = mapped_column(String(32), default="threshold", nullable=False)  # threshold, rate, baseline, absence
    operator: Mapped[str] = mapped_column(String(16), default="gt", nullable=False)  # gt, gte, lt, lte, eq, neq
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    severity: Mapped[str] = mapped_column(String(32), default="warning", nullable=False)  # info, warning, average, high, critical
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expression_dsl: Mapped[str | None] = mapped_column(Text, nullable=True)  # Ruby DSL or expression
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    rule_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    check_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(32), default="warning", nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)  # active, acknowledged, resolved
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    triggered_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    dismissed_episode_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    acknowledgements = relationship("Acknowledgement", back_populates="alert", cascade="all, delete-orphan")


class Acknowledgement(Base):
    __tablename__ = "acknowledgements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    alert_id: Mapped[int] = mapped_column(Integer, ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    comment: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    alert = relationship("Alert", back_populates="acknowledgements")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, index=True)
    check_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # state_change, alert_trigger, alert_resolve, trap
    client_event_key: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    severity: Mapped[str] = mapped_column(String(32), default="info", nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)  # email, slack, teams, telegram, webhook, pagerduty
    config: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class EscalationPolicy(Base):
    __tablename__ = "escalation_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    steps: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)  # list of {delay_minutes: int, channel_ids: []}
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    start_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    end_time: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(32), default="all", nullable=False)  # all, device, group, site
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

