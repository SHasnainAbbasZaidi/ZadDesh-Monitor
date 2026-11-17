from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from zaddesh_core.database import Base


class EmailSettings(Base):
    __tablename__ = "email_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    secret: Mapped[str] = mapped_column(Text, default="")
    generation: Mapped[int] = mapped_column(default=0)
    health: Mapped[str] = mapped_column(String(24), default="untested")
    last_error: Mapped[str | None] = mapped_column(String(240))
    last_test: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str | None] = mapped_column(String(64))
    cursor: Mapped[str | None] = mapped_column(String(20))
    scan_from: Mapped[int] = mapped_column(BigInteger, default=0)
    poll_after: Mapped[int] = mapped_column(BigInteger, default=0)
    last_poll: Mapped[int | None] = mapped_column(BigInteger)
    poll_error: Mapped[str | None] = mapped_column(String(240))
    send_after: Mapped[int] = mapped_column(BigInteger, default=0)
    test_after: Mapped[int] = mapped_column(BigInteger, default=0)
    prune_after: Mapped[int] = mapped_column(BigInteger, default=0)


class EmailRecipient(Base):
    __tablename__ = "email_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    address_key: Mapped[str] = mapped_column(String(254), unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)


class EmailRule(Base):
    __tablename__ = "email_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON)


class EmailProblem(Base):
    __tablename__ = "email_problems"
    __table_args__ = (Index("ix_email_problems_refresh", "source", "resolved_at", "observed_at"),)

    key: Mapped[str] = mapped_column(String(85), primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[str] = mapped_column(String(20))
    observed_at: Mapped[int] = mapped_column(BigInteger)
    resolved_at: Mapped[int | None] = mapped_column(BigInteger)
    severity: Mapped[int]
    notified_severity: Mapped[int] = mapped_column(default=-1)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON)


class EmailDelivery(Base):
    __tablename__ = "email_deliveries"
    __table_args__ = (
        Index("ix_email_deliveries_queue", "status", "next_attempt"),
        Index("ix_email_deliveries_problem", "problem_key", "status"),
        Index("ix_email_deliveries_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    problem_key: Mapped[str | None] = mapped_column(String(85))
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("email_recipients.id", ondelete="SET NULL"))
    address: Mapped[str] = mapped_column(String(254))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    severity: Mapped[int | None]
    created_at: Mapped[int] = mapped_column(BigInteger)
    next_attempt: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[int | None] = mapped_column(BigInteger)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(240))
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    subject: Mapped[str] = mapped_column(String(512))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class EmailAttempt(Base):
    __tablename__ = "email_attempts"
    __table_args__ = (Index("ix_email_attempts_at", "at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("email_deliveries.id", ondelete="CASCADE"), index=True)
    at: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(240))
