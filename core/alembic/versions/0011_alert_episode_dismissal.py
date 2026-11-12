"""Track alerts removed from the incident workspace by episode."""
import sqlalchemy as sa
from alembic import op

revision = "0011_alert_episode_dismissal"
down_revision = "0010_zabbix_host_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("dismissed_episode_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("alerts", "dismissed_episode_at")
