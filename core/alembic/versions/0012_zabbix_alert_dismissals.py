"""Persist date ranges dismissed from the Zabbix alert view."""
import sqlalchemy as sa
from alembic import op

revision = "0012_zabbix_alert_dismissals"
down_revision = "0011_alert_episode_dismissal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("zabbix_alert_dismissals", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("source_key", sa.String(64), nullable=False),
                    sa.Column("start", sa.DateTime(), nullable=False),
                    sa.Column("end", sa.DateTime(), nullable=False))
    op.create_index("ix_zabbix_alert_dismissals_source_key", "zabbix_alert_dismissals", ["source_key"])


def downgrade() -> None:
    op.drop_index("ix_zabbix_alert_dismissals_source_key", table_name="zabbix_alert_dismissals")
    op.drop_table("zabbix_alert_dismissals")
