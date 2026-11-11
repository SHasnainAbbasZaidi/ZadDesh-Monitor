"""Identify the built-in automatic topology diagram."""
import sqlalchemy as sa
from alembic import op

revision = "0008_automatic_topology"
down_revision = "0007_incident_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("topology_diagrams", sa.Column("automatic_key", sa.String(32), nullable=True))
    op.create_index("ix_topology_diagrams_automatic_key", "topology_diagrams", ["automatic_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_topology_diagrams_automatic_key", table_name="topology_diagrams")
    op.drop_column("topology_diagrams", "automatic_key")
