"""Keep removed Zabbix hosts out of the ZadDesh inventory."""
import sqlalchemy as sa
from alembic import op

revision = "0013_zabbix_hidden_hosts"
down_revision = "0012_zabbix_alert_dismissals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("zabbix_hidden_hosts", sa.Column("source_key", sa.String(64), primary_key=True),
                    sa.Column("host_id", sa.String(20), primary_key=True))


def downgrade() -> None:
    op.drop_table("zabbix_hidden_hosts")
