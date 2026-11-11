"""initial schema

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-11-04 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Auth & RBAC
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roles_id"), "roles", ["id"], unique=False)
    op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("totp_secret", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_tokens_id"), "api_tokens", ["id"], unique=False)
    op.create_index(op.f("ix_api_tokens_prefix"), "api_tokens", ["prefix"], unique=False)
    op.create_index(op.f("ix_api_tokens_token_hash"), "api_tokens", ["token_hash"], unique=True)

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"], unique=False)
    op.create_index(op.f("ix_audit_log_created_at"), "audit_log", ["created_at"], unique=False)
    op.create_index(op.f("ix_audit_log_entity_type"), "audit_log", ["entity_type"], unique=False)
    op.create_index(op.f("ix_audit_log_id"), "audit_log", ["id"], unique=False)

    # 2. Infrastructure
    op.create_table(
        "sites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("latitude", sa.String(length=32), nullable=True),
        sa.Column("longitude", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sites_id"), "sites", ["id"], unique=False)
    op.create_index(op.f("ix_sites_name"), "sites", ["name"], unique=True)
    op.create_index(op.f("ix_sites_slug"), "sites", ["slug"], unique=True)

    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["groups.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_groups_id"), "groups", ["id"], unique=False)
    op.create_index(op.f("ix_groups_name"), "groups", ["name"], unique=False)
    op.create_index(op.f("ix_groups_slug"), "groups", ["slug"], unique=True)

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("site_id", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("os", sa.String(length=64), nullable=True),
        sa.Column("vendor", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("uptime_seconds", sa.Integer(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=True),
        sa.Column("is_monitored", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_devices_id"), "devices", ["id"], unique=False)
    op.create_index(op.f("ix_devices_ip"), "devices", ["ip"], unique=False)
    op.create_index(op.f("ix_devices_name"), "devices", ["name"], unique=False)
    op.create_index(op.f("ix_devices_status"), "devices", ["status"], unique=False)

    op.create_table(
        "device_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("credential_type", sa.String(length=32), nullable=False),
        sa.Column("encrypted_data", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_device_credentials_id"), "device_credentials", ["id"], unique=False)

    op.create_table(
        "collectors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("auth_token_hash", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("registered_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_collectors_auth_token_hash"), "collectors", ["auth_token_hash"], unique=False)
    op.create_index(op.f("ix_collectors_id"), "collectors", ["id"], unique=False)
    op.create_index(op.f("ix_collectors_name"), "collectors", ["name"], unique=True)

    # 3. Checks
    op.create_table(
        "check_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("check_type", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("default_params", sa.JSON(), nullable=False),
        sa.Column("default_interval", sa.Integer(), nullable=False),
        sa.Column("default_timeout", sa.Integer(), nullable=False),
        sa.Column("default_retries", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_check_templates_check_type"), "check_templates", ["check_type"], unique=False)
    op.create_index(op.f("ix_check_templates_id"), "check_templates", ["id"], unique=False)
    op.create_index(op.f("ix_check_templates_name"), "check_templates", ["name"], unique=True)

    op.create_table(
        "checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("check_type", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["check_templates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_checks_check_type"), "checks", ["check_type"], unique=False)
    op.create_index(op.f("ix_checks_id"), "checks", ["id"], unique=False)
    op.create_index(op.f("ix_checks_name"), "checks", ["name"], unique=False)

    op.create_table(
        "check_state",
        sa.Column("check_id", sa.Integer(), nullable=False),
        sa.Column("current_status", sa.String(length=32), nullable=False),
        sa.Column("last_check_time", sa.DateTime(), nullable=True),
        sa.Column("last_change_time", sa.DateTime(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("flap_detected", sa.Boolean(), nullable=False),
        sa.Column("message", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(["check_id"], ["checks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("check_id"),
    )
    op.create_index(op.f("ix_check_state_current_status"), "check_state", ["current_status"], unique=False)

    # 4. Metrics
    op.create_table(
        "metrics_raw",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=True),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["check_id"], ["checks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_metrics_raw_id"), "metrics_raw", ["id"], unique=False)
    op.create_index(op.f("ix_metrics_raw_device_id"), "metrics_raw", ["device_id"], unique=False)
    op.create_index(op.f("ix_metrics_raw_check_id"), "metrics_raw", ["check_id"], unique=False)
    op.create_index(op.f("ix_metrics_raw_metric_key"), "metrics_raw", ["metric_key"], unique=False)
    op.create_index(op.f("ix_metrics_raw_timestamp"), "metrics_raw", ["timestamp"], unique=False)
    op.create_index("ix_metrics_raw_lookup", "metrics_raw", ["device_id", "metric_key", "timestamp"])

    for rollup in ["1m", "5m", "1h", "1d"]:
        tbl = f"metrics_rollup_{rollup}"
        op.create_table(
            tbl,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("device_id", sa.Integer(), nullable=False),
            sa.Column("check_id", sa.Integer(), nullable=True),
            sa.Column("metric_key", sa.String(length=128), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("val_avg", sa.Float(), nullable=False),
            sa.Column("val_min", sa.Float(), nullable=False),
            sa.Column("val_max", sa.Float(), nullable=False),
            sa.Column("val_sum", sa.Float(), nullable=False),
            sa.Column("val_count", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["check_id"], ["checks.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f(f"ix_{tbl}_id"), tbl, ["id"], unique=False)
        op.create_index(f"ix_{tbl}_lookup", tbl, ["device_id", "metric_key", "timestamp"])

    # 5. Alerts & Events
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("metric_key", sa.String(length=128), nullable=True),
        sa.Column("condition_type", sa.String(length=32), nullable=False),
        sa.Column("operator", sa.String(length=16), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("expression_dsl", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alert_rules_id"), "alert_rules", ["id"], unique=False)
    op.create_index(op.f("ix_alert_rules_name"), "alert_rules", ["name"], unique=False)

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("triggered_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["check_id"], ["checks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["alert_rules.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_alerts_id"), "alerts", ["id"], unique=False)
    op.create_index(op.f("ix_alerts_device_id"), "alerts", ["device_id"], unique=False)
    op.create_index(op.f("ix_alerts_severity"), "alerts", ["severity"], unique=False)
    op.create_index(op.f("ix_alerts_status"), "alerts", ["status"], unique=False)
    op.create_index(op.f("ix_alerts_triggered_at"), "alerts", ["triggered_at"], unique=False)

    op.create_table(
        "acknowledgements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("comment", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_acknowledgements_id"), "acknowledgements", ["id"], unique=False)
    op.create_index(op.f("ix_acknowledgements_alert_id"), "acknowledgements", ["alert_id"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("check_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["check_id"], ["checks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_id"), "events", ["id"], unique=False)
    op.create_index(op.f("ix_events_event_type"), "events", ["event_type"], unique=False)
    op.create_index(op.f("ix_events_severity"), "events", ["severity"], unique=False)
    op.create_index(op.f("ix_events_timestamp"), "events", ["timestamp"], unique=False)

    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "escalation_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    # 6. SNMP
    op.create_table(
        "snmp_mibs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("parsed_metadata", sa.JSON(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "snmp_traps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("oid", sa.String(length=255), nullable=False),
        sa.Column("trap_type", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 7. System & Dashboards
    op.create_table(
        "dashboards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("layout", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "widgets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dashboard_id", sa.Integer(), nullable=False),
        sa.Column("widget_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("grid_position", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["dashboard_id"], ["dashboards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "saved_views",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("page", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("report_type", sa.String(length=64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "report_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("cron_expr", sa.String(length=64), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("report_schedules")
    op.drop_table("reports")
    op.drop_table("saved_views")
    op.drop_table("widgets")
    op.drop_table("dashboards")
    op.drop_table("snmp_traps")
    op.drop_table("snmp_mibs")
    op.drop_table("maintenance_windows")
    op.drop_table("escalation_policies")
    op.drop_table("notification_channels")
    op.drop_table("events")
    op.drop_table("acknowledgements")
    op.drop_table("alerts")
    op.drop_table("alert_rules")
    for rollup in ["1d", "1h", "5m", "1m"]:
        op.drop_table(f"metrics_rollup_{rollup}")
    op.drop_table("metrics_raw")
    op.drop_table("check_state")
    op.drop_table("checks")
    op.drop_table("check_templates")
    op.drop_table("collectors")
    op.drop_table("device_credentials")
    op.drop_table("devices")
    op.drop_table("groups")
    op.drop_table("sites")
    op.drop_table("audit_log")
    op.drop_table("api_tokens")
    op.drop_table("users")
    op.drop_table("roles")

