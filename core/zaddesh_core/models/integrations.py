from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from zaddesh_core.database import Base


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    api_url: Mapped[str] = mapped_column(String(2048))
    auth_type: Mapped[str] = mapped_column(String(16))
    username: Mapped[str] = mapped_column(String(256))
    header_name: Mapped[str] = mapped_column(String(128))
    encrypted_secret: Mapped[str] = mapped_column(Text)
