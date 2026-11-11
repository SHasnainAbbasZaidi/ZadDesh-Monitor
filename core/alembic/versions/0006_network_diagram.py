"""Add Network Diagram Beta persistence."""
from alembic import op
import sqlalchemy as sa

revision = "0006_network_diagram"
down_revision = "0005_incident_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    def identifier():
        return sa.Column("id", sa.Integer, primary_key=True)

    def reference(name, table, ondelete="CASCADE", nullable=False):
        return sa.Column(name, sa.Integer, sa.ForeignKey(f"{table}.id", ondelete=ondelete), nullable=nullable)

    def string(name, length, nullable=False):
        return sa.Column(name, sa.String(length), nullable=nullable)

    def required(name, kind):
        return sa.Column(name, kind, nullable=False)

    def diagram_id():
        return reference("diagram_id", "topology_diagrams")

    op.create_table("topology_diagrams", identifier(), string("name", 100), required("layout", sa.JSON), required("published", sa.Boolean))
    op.create_table("topology_nodes", identifier(), diagram_id(), string("key", 255), string("label", 255),
                    reference("device_id", "devices", "SET NULL", True), string("kind", 32), required("manual", sa.Boolean),
                    required("metadata_json", sa.JSON), sa.UniqueConstraint("diagram_id", "key"))
    op.create_table("topology_links", identifier(), diagram_id(), string("key", 64), reference("source_id", "topology_nodes"),
                    reference("target_id", "topology_nodes"), string("source_port", 255), string("target_port", 255),
                    string("method", 16), string("status", 16), sa.Column("speed", sa.BigInteger),
                    sa.Column("utilization", sa.Float), required("manual", sa.Boolean), required("evidence", sa.JSON),
                    sa.UniqueConstraint("diagram_id", "key"))
    op.create_table("topology_discovery_runs", identifier(), diagram_id(), reference("check_id", "checks"),
                    reference("collector_id", "collectors", "SET NULL", True), string("status", 16),
                    required("created_at", sa.DateTime), sa.Column("started_at", sa.DateTime),
                    sa.Column("finished_at", sa.DateTime), sa.Column("message", sa.Text), string("lease", 64, True))
    op.create_index("ix_topology_discovery_runs_diagram_id", "topology_discovery_runs", ["diagram_id"])
    op.create_table("topology_changes", identifier(), diagram_id(), string("kind", 32), string("description", 512),
                    string("decision", 16), required("created_at", sa.DateTime))
    op.create_index("ix_topology_changes_diagram_id", "topology_changes", ["diagram_id"])


def downgrade() -> None:
    for table in ("topology_changes", "topology_discovery_runs", "topology_links", "topology_nodes", "topology_diagrams"):
        op.drop_table(table)
