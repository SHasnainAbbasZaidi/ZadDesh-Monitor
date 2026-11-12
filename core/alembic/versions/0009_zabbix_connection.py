"""Store an encrypted Zabbix connection."""
import sqlalchemy as sa
from alembic import op

revision = "0009_zabbix_connection"
down_revision = "0008_automatic_topology"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zabbix_connection",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("api_url", sa.String(2048), nullable=False),
        sa.Column("encrypted_token", sa.Text, nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("zabbix_connection")
