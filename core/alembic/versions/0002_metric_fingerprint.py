"""Add a unique key for retry-safe metric ingestion.

Revision ID: 0002_metric_fingerprint
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_metric_fingerprint"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("metrics_raw", sa.Column("fingerprint", sa.String(length=64), nullable=True))
    op.create_index("uq_metrics_raw_fingerprint", "metrics_raw", ["fingerprint"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_metrics_raw_fingerprint", table_name="metrics_raw")
    op.drop_column("metrics_raw", "fingerprint")
