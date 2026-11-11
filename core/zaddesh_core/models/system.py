from __future__ import annotations

import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import relationship
from zaddesh_core.database import Base


class Dashboard(Base):
    __tablename__ = "dashboards"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(128), nullable=False)
    description = Column(String(255), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    is_public = Column(Boolean, default=False, nullable=False)
    layout = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    widgets = relationship("Widget", back_populates="dashboard", cascade="all, delete-orphan")


class Widget(Base):
    __tablename__ = "widgets"

    id = Column(Integer, primary_key=True, index=True)
    dashboard_id = Column(Integer, ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False)
    widget_type = Column(String(64), nullable=False)  # line_chart, gauge, status_grid, top_n, sparkline
    title = Column(String(128), nullable=False)
    config = Column(JSON, default=dict, nullable=False)
    grid_position = Column(JSON, default=dict, nullable=False)

    dashboard = relationship("Dashboard", back_populates="widgets")


class SavedView(Base):
    __tablename__ = "saved_views"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    page = Column(String(64), nullable=False)  # devices, alerts, checks
    name = Column(String(128), nullable=False)
    filters = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(128), nullable=False)
    report_type = Column(String(64), nullable=False)  # sla, availability, top_n, capacity
    config = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    schedules = relationship("ReportSchedule", back_populates="report", cascade="all, delete-orphan")


class ReportSchedule(Base):
    __tablename__ = "report_schedules"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
    cron_expr = Column(String(64), nullable=False)
    recipients = Column(JSON, default=list, nullable=False)
    is_enabled = Column(Boolean, default=True, nullable=False)

    report = relationship("Report", back_populates="schedules")

