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


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permissions: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    users = relationship("User", back_populates="role_rel")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(50), default="viewer", nullable=False)
    role_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )

    role_rel = relationship("Role", back_populates="users")
    api_tokens = relationship("ApiToken", back_populates="user", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="user")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="api_tokens")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[Any] = mapped_column(JSON, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, nullable=False, index=True)

    user = relationship("User", back_populates="audit_logs")

