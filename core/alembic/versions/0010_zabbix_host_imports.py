"""Link imported Zabbix hosts to their local devices."""
import sqlalchemy as sa
from alembic import op

revision = "0010_zabbix_host_imports"
down_revision = "0009_zabbix_connection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zabbix_host_imports",
        sa.Column("source_key", sa.String(64), primary_key=True),
        sa.Column("host_id", sa.String(20), primary_key=True),
        sa.Column("device_id", sa.Integer, sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index("ix_zabbix_host_imports_device_id", "zabbix_host_imports", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_zabbix_host_imports_device_id", table_name="zabbix_host_imports")
    op.drop_table("zabbix_host_imports")
