"""Store encrypted API integration connections."""
import sqlalchemy as sa
from alembic import op

revision = "0015_api_integrations"
down_revision = "0014_email_alerts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "integration_connections",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("api_url", sa.String(2048), nullable=False),
        sa.Column("auth_type", sa.String(16), nullable=False),
        sa.Column("username", sa.String(256), nullable=False),
        sa.Column("header_name", sa.String(128), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("integration_connections")
