"""Align database indexes with the ORM and index check joins.

Revision ID: 0003_query_indexes
Revises: 0002_metric_fingerprint
"""
from alembic import op

revision = "0003_query_indexes"
down_revision = "0002_metric_fingerprint"
branch_labels = None
depends_on = None

INDEXES = (
    ("alert_rules", "metric_key"), ("alerts", "check_id"), ("checks", "device_id"),
    ("dashboards", "id"), ("escalation_policies", "id"),
    ("events", "check_id"), ("events", "device_id"),
    ("maintenance_windows", "end_time"), ("maintenance_windows", "id"),
    ("maintenance_windows", "start_time"), ("notification_channels", "id"),
    ("report_schedules", "id"), ("reports", "id"), ("saved_views", "id"),
    ("snmp_mibs", "id"), ("snmp_traps", "id"), ("snmp_traps", "oid"),
    ("snmp_traps", "received_at"), ("snmp_traps", "source_ip"), ("widgets", "id"),
) + tuple(
    (f"metrics_rollup_{window}", column)
    for window in ("1m", "5m", "1h", "1d")
    for column in ("check_id", "device_id", "metric_key", "timestamp")
)


def upgrade() -> None:
    for table, column in INDEXES:
        op.create_index(f"ix_{table}_{column}", table, [column], if_not_exists=True)
    op.create_index("ix_metrics_raw_check", "metrics_raw", ["check_id", "timestamp"], if_not_exists=True)
    op.create_index("ix_snmp_mibs_name", "snmp_mibs", ["name"], unique=True, if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_snmp_mibs_name", table_name="snmp_mibs")
    op.drop_index("ix_metrics_raw_check", table_name="metrics_raw")
    for table, column in reversed(INDEXES):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
