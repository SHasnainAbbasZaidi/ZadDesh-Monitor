from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from zaddesh_core.database import Base


class ZabbixConnection(Base):
    __tablename__ = "zabbix_connection"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_url: Mapped[str] = mapped_column(String(2048))
    encrypted_token: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(64))


class ZabbixHostImport(Base):
    __tablename__ = "zabbix_host_imports"

    source_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)


class ZabbixAlertDismissal(Base):
    __tablename__ = "zabbix_alert_dismissals"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64), index=True)
    start: Mapped[datetime]
    end: Mapped[datetime]
