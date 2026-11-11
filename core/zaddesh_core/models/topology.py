from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from zaddesh_core.database import Base


class TopologyDiagram(Base):
    __tablename__ = "topology_diagrams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    layout: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    published: Mapped[bool] = mapped_column(default=False)
    automatic_key: Mapped[str | None] = mapped_column(String(32), index=True, unique=True)


class TopologyNode(Base):
    __tablename__ = "topology_nodes"
    __table_args__ = (UniqueConstraint("diagram_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    diagram_id: Mapped[int] = mapped_column(ForeignKey("topology_diagrams.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(255))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), default="unmanaged")
    manual: Mapped[bool] = mapped_column(default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TopologyLink(Base):
    __tablename__ = "topology_links"
    __table_args__ = (UniqueConstraint("diagram_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    diagram_id: Mapped[int] = mapped_column(ForeignKey("topology_diagrams.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[int] = mapped_column(ForeignKey("topology_nodes.id", ondelete="CASCADE"))
    target_id: Mapped[int] = mapped_column(ForeignKey("topology_nodes.id", ondelete="CASCADE"))
    source_port: Mapped[str] = mapped_column(String(255))
    target_port: Mapped[str] = mapped_column(String(255))
    method: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    speed: Mapped[int | None] = mapped_column(BigInteger)
    utilization: Mapped[float | None]
    manual: Mapped[bool] = mapped_column(default=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TopologyRun(Base):
    __tablename__ = "topology_discovery_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    diagram_id: Mapped[int] = mapped_column(ForeignKey("topology_diagrams.id", ondelete="CASCADE"), index=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("checks.id", ondelete="CASCADE"))
    collector_id: Mapped[int | None] = mapped_column(ForeignKey("collectors.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    message: Mapped[str | None] = mapped_column(Text)
    lease: Mapped[str | None] = mapped_column(String(64))


class TopologyChange(Base):
    __tablename__ = "topology_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    diagram_id: Mapped[int] = mapped_column(ForeignKey("topology_diagrams.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(512))
    decision: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime]
