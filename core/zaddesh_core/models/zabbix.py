from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from zaddesh_core.database import Base


class ZabbixConnection(Base):
    __tablename__ = "zabbix_connection"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_url: Mapped[str] = mapped_column(String(2048))
    encrypted_token: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(64))
