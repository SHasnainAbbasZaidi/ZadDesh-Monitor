"""Deduplicate SNMP client issue logs across on-demand snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0018_client_issue_logs"
down_revision = "0017_workstation_inventory"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("events", sa.Column("client_event_key", sa.String(64), nullable=True))
    op.create_index("ix_events_client_event_key", "events", ["client_event_key"], unique=True)


def downgrade():
    op.drop_index("ix_events_client_event_key", table_name="events")
    with op.batch_alter_table("events") as batch:
        batch.drop_column("client_event_key")
