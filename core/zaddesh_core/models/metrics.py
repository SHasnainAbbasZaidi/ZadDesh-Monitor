from __future__ import annotations

import datetime
from typing import Any
from sqlalchemy import (
    Integer,
    Float,
    String,
    DateTime,
    ForeignKey,
    JSON,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from zaddesh_core.database import Base


class MetricRaw(Base):
    __tablename__ = "metrics_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    check_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), nullable=True, index=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    tags: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)

    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("uq_metrics_raw_fingerprint", "fingerprint", unique=True),
        Index("ix_metrics_raw_lookup", "device_id", "metric_key", "timestamp"),
        Index("ix_metrics_raw_check", "check_id", "timestamp"),
    )


class MetricRollup1m(Base):
    __tablename__ = "metrics_rollup_1m"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    check_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), nullable=True, index=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    val_avg: Mapped[float] = mapped_column(Float, nullable=False)
    val_min: Mapped[float] = mapped_column(Float, nullable=False)
    val_max: Mapped[float] = mapped_column(Float, nullable=False)
    val_sum: Mapped[float] = mapped_column(Float, nullable=False)
    val_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_metrics_rollup_1m_lookup", "device_id", "metric_key", "timestamp"),
    )


class MetricRollup5m(Base):
    __tablename__ = "metrics_rollup_5m"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    check_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), nullable=True, index=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    val_avg: Mapped[float] = mapped_column(Float, nullable=False)
    val_min: Mapped[float] = mapped_column(Float, nullable=False)
    val_max: Mapped[float] = mapped_column(Float, nullable=False)
    val_sum: Mapped[float] = mapped_column(Float, nullable=False)
    val_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_metrics_rollup_5m_lookup", "device_id", "metric_key", "timestamp"),
    )


class MetricRollup1h(Base):
    __tablename__ = "metrics_rollup_1h"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    check_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), nullable=True, index=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    val_avg: Mapped[float] = mapped_column(Float, nullable=False)
    val_min: Mapped[float] = mapped_column(Float, nullable=False)
    val_max: Mapped[float] = mapped_column(Float, nullable=False)
    val_sum: Mapped[float] = mapped_column(Float, nullable=False)
    val_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_metrics_rollup_1h_lookup", "device_id", "metric_key", "timestamp"),
    )


class MetricRollup1d(Base):
    __tablename__ = "metrics_rollup_1d"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    check_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"), nullable=True, index=True)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, index=True)
    val_avg: Mapped[float] = mapped_column(Float, nullable=False)
    val_min: Mapped[float] = mapped_column(Float, nullable=False)
    val_max: Mapped[float] = mapped_column(Float, nullable=False)
    val_sum: Mapped[float] = mapped_column(Float, nullable=False)
    val_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_metrics_rollup_1d_lookup", "device_id", "metric_key", "timestamp"),
    )

