"""Add on-demand SNMP process and service snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0017_workstation_inventory"
down_revision = "0016_device_priority"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workstation_inventory",
        sa.Column("device_id", sa.Integer, sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("check_id", sa.Integer, sa.ForeignKey("checks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime), sa.Column("collected_at", sa.DateTime),
        sa.Column("collector_id", sa.Integer, sa.ForeignKey("collectors.id", ondelete="SET NULL")),
        sa.Column("lease", sa.String(48)), sa.Column("data", sa.JSON, nullable=False), sa.Column("message", sa.Text))


def downgrade():
    op.drop_table("workstation_inventory")
