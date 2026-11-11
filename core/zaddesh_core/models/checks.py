from __future__ import annotations

import datetime
from typing import Any
from sqlalchemy import (
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from zaddesh_core.database import Base


class CheckTemplate(Base):
    __tablename__ = "check_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    check_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # icmp, http, tcp, udp, snmp
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_params: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)
    default_interval: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    default_timeout: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    default_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    checks = relationship("Check", back_populates="template")


class Check(Base):
    __tablename__ = "checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("check_templates.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    check_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # icmp, http, tcp, udp, snmp
    target: Mapped[str] = mapped_column(String(255), nullable=False)  # IP, hostname, URL, port
    params: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )

    device = relationship("Device", back_populates="checks")
    template = relationship("CheckTemplate", back_populates="checks")
    state = relationship("CheckState", back_populates="check", uselist=False, cascade="all, delete-orphan")


class CheckState(Base):
    __tablename__ = "check_state"

    check_id: Mapped[int] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), primary_key=True)
    current_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False, index=True)  # ok, warning, critical, unknown
    last_check_time: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_change_time: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    flap_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    check = relationship("Check", back_populates="state")

