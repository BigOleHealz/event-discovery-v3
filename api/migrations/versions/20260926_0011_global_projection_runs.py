"""Allow the global graph projection to log a run without a market (Phase 5a)."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260926_0011"
down_revision: str | None = "20260922_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("run", "market_id", nullable=True, schema="ingest")
    op.create_check_constraint(
        "ck_run_market_scope", "run",
        "market_id IS NOT NULL OR COALESCE(airflow_dag_id = 'project_to_neo4j', false)",
        schema="ingest",
    )


def downgrade() -> None:
    # Refuse to discard or invent a market for existing global run history.
    # On a populated database, global run records must be handled before downgrade.
    op.alter_column("run", "market_id", nullable=False, schema="ingest")
    op.drop_constraint("ck_run_market_scope", "run", schema="ingest", type_="check")
