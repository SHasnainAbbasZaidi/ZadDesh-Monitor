"""Persist incident webhook deliveries and retries."""
from alembic import op
import sqlalchemy as sa

revision = "0007_incident_deliveries"
down_revision = "0006_network_diagram"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_deliveries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("alert_id", sa.Integer, sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_attempt", sa.DateTime, nullable=False),
        sa.Column("delivered_at", sa.DateTime),
        sa.Column("outcome", sa.String(64)),
    )
    op.create_index("ix_incident_deliveries_alert_id", "incident_deliveries", ["alert_id"])
    op.create_index("ix_incident_deliveries_next_attempt", "incident_deliveries", ["next_attempt"])


def downgrade() -> None:
    op.drop_table("incident_deliveries")
