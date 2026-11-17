"""Persist the admin SMTP configuration, Zabbix cursor, and delivery queue."""
import sqlalchemy as sa
from alembic import op

revision = "0014_email_alerts"
down_revision = "0013_zabbix_hidden_hosts"
branch_labels = None
depends_on = None


def column(name, kind, **kwargs):
    return sa.Column(name, kind, nullable=kwargs.pop("nullable", False), **kwargs)


def upgrade():
    integer, timestamp = sa.Integer, sa.BigInteger
    op.create_table("email_settings", column("id", integer, primary_key=True), column("config", sa.JSON),
                    column("secret", sa.Text), column("generation", integer), column("health", sa.String(24)),
                    column("last_error", sa.String(240), nullable=True), column("last_test", timestamp, nullable=True),
                    column("source", sa.String(64), nullable=True), column("cursor", sa.String(20), nullable=True),
                    column("scan_from", timestamp), column("poll_after", timestamp), column("last_poll", timestamp, nullable=True),
                    column("poll_error", sa.String(240), nullable=True), column("send_after", timestamp),
                    column("test_after", timestamp), column("prune_after", timestamp))
    op.create_table("email_recipients", column("id", integer, primary_key=True),
                    column("address_key", sa.String(254), unique=True), column("config", sa.JSON))
    op.create_table("email_rules", column("id", integer, primary_key=True), column("config", sa.JSON))
    op.create_table("email_templates", column("kind", sa.String(16), primary_key=True), column("config", sa.JSON))
    op.create_table("email_problems", column("key", sa.String(85), primary_key=True), column("source", sa.String(64)),
                    column("event_id", sa.String(20)), column("observed_at", timestamp), column("resolved_at", timestamp, nullable=True),
                    column("severity", integer), column("notified_severity", integer), column("suppressed", sa.Boolean), column("context", sa.JSON))
    op.create_index("ix_email_problems_refresh", "email_problems", ["source", "resolved_at", "observed_at"])
    op.create_table("email_deliveries", column("id", integer, primary_key=True), column("dedup_key", sa.String(64), unique=True),
                    column("problem_key", sa.String(85), nullable=True), column("recipient_id", integer, nullable=True),
                    column("address", sa.String(254)), column("kind", sa.String(16)), column("status", sa.String(16)),
                    column("severity", integer, nullable=True), column("created_at", timestamp), column("next_attempt", timestamp),
                    column("sent_at", timestamp, nullable=True), column("attempts", integer),
                    column("last_error", sa.String(240), nullable=True), column("uncertain", sa.Boolean), column("subject", sa.String(512)),
                    column("payload", sa.JSON), sa.ForeignKeyConstraint(["recipient_id"], ["email_recipients.id"], ondelete="SET NULL"))
    op.create_index("ix_email_deliveries_queue", "email_deliveries", ["status", "next_attempt"])
    op.create_index("ix_email_deliveries_problem", "email_deliveries", ["problem_key", "status"])
    op.create_index("ix_email_deliveries_created", "email_deliveries", ["created_at"])
    op.create_table("email_attempts", column("id", integer, primary_key=True), column("delivery_id", integer),
                    column("at", timestamp), column("status", sa.String(16)), column("outcome", sa.String(240), nullable=True),
                    sa.ForeignKeyConstraint(["delivery_id"], ["email_deliveries.id"], ondelete="CASCADE"))
    op.create_index("ix_email_attempts_delivery_id", "email_attempts", ["delivery_id"])
    op.create_index("ix_email_attempts_at", "email_attempts", ["at"])


def downgrade():
    for table in ("email_attempts", "email_deliveries", "email_problems", "email_templates", "email_rules", "email_recipients", "email_settings"):
        op.drop_table(table)
