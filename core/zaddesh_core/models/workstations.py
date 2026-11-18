from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from zaddesh_core.database import Base


class WorkstationInventory(Base):
    __tablename__ = "workstation_inventory"

    device_id: Mapped[int] = mapped_column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    check_id: Mapped[int] = mapped_column(Integer, ForeignKey("checks.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    collector_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("collectors.id", ondelete="SET NULL"), nullable=True)
    lease: Mapped[str | None] = mapped_column(String(48), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
