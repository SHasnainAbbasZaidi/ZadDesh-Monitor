"""Preserve existing infrastructure priority and allow quiet workstation collection."""
import sqlalchemy as sa
from alembic import op

revision = "0016_device_priority"
down_revision = "0015_api_integrations"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("is_priority", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("devices", sa.Column("collection_mode", sa.String(16), nullable=False, server_default="scheduled"))


def downgrade():
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("is_priority")
        batch.drop_column("collection_mode")
