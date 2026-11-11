"""Track a session version for each user.

Revision ID: 0004_user_session_version
Revises: 0003_query_indexes
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_user_session_version"
down_revision = "0003_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("session_version", sa.Integer(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("users", "session_version")
