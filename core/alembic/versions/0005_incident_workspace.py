"""Add incident workspace metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0005_incident_workspace"
down_revision = "0004_user_session_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_incidents",
        sa.Column("alert_id", sa.Integer, sa.ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("correlation_key", sa.String(128), nullable=False, unique=True),
        sa.Column("occurrence_count", sa.Integer, nullable=False),
        sa.Column("flapping", sa.Boolean, nullable=False),
        sa.Column("acknowledged_at", sa.DateTime),
        sa.Column("silenced_until", sa.DateTime),
        sa.Column("suppression", sa.String(128)),
        sa.Column("transitions", sa.JSON, nullable=False),
    )
    op.create_table(
        "alert_timeline",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("alert_id", sa.Integer, sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(64)),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
    )
    op.create_index("ix_alert_timeline_alert_id", "alert_timeline", ["alert_id"])
    op.create_table(
        "incident_parents",
        sa.Column("device_id", sa.Integer, sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_table(
        "incident_filters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("values", sa.JSON, nullable=False),
    )
    op.create_index("ix_incident_filters_user_id", "incident_filters", ["user_id"])


def downgrade() -> None:
    for table in ("incident_filters", "incident_parents", "alert_timeline", "alert_incidents"):
        op.drop_table(table)
