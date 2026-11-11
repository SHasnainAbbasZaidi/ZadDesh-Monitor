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
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from zaddesh_core.database import Base


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[str | None] = mapped_column(String(32), nullable=True)
    longitude: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    devices = relationship("Device", back_populates="site")
    collectors = relationship("Collector", back_populates="site")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    children = relationship("Group", backref="parent", remote_side=[id])
    devices = relationship("Device", back_populates="group")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ip: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    site_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False, index=True)  # up, down, warning, unknown, unmanaged
    os: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uptime_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_seen: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    is_monitored: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )

    site = relationship("Site", back_populates="devices")
    group = relationship("Group", back_populates="devices")
    credentials = relationship("DeviceCredential", back_populates="device", cascade="all, delete-orphan")
    checks = relationship("Check", back_populates="device", cascade="all, delete-orphan")


class DeviceCredential(Base):
    __tablename__ = "device_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    credential_type: Mapped[str] = mapped_column(String(32), nullable=False)  # snmp_v2, snmp_v3, ssh, http
    encrypted_data: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    device = relationship("Device", back_populates="credentials")


class Collector(Base):
    __tablename__ = "collectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    auth_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    site_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[str] = mapped_column(String(32), default="0.1.0", nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="offline", nullable=False)  # online, offline, degraded
    last_heartbeat_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    registered_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    site = relationship("Site", back_populates="collectors")

