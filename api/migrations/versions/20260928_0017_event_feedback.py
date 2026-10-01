"""Persist in-app feedback requests and allow their global audit runs (Phase 6c)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0017"
down_revision: str | None = "20260928_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_feedback_request",
        sa.Column(
            "attendance_id",
            sa.Uuid(),
            sa.ForeignKey("attendance.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint("ck_attendance_rating", "attendance", "rating BETWEEN 1 AND 5")
    op.drop_constraint("ck_run_market_scope", "run", schema="ingest", type_="check")
    op.create_check_constraint(
        "ck_run_market_scope",
        "run",
        "market_id IS NOT NULL OR COALESCE(airflow_dag_id IN "
        "('project_to_neo4j', 'request_event_feedback'), false)",
        schema="ingest",
    )


def downgrade() -> None:
    # Fail rather than discard or mislabel global feedback-job audit history.
    op.drop_constraint("ck_run_market_scope", "run", schema="ingest", type_="check")
    op.create_check_constraint(
        "ck_run_market_scope",
        "run",
        "market_id IS NOT NULL OR COALESCE(airflow_dag_id = 'project_to_neo4j', false)",
        schema="ingest",
    )
    op.drop_constraint("ck_attendance_rating", "attendance", type_="check")
    op.drop_table("event_feedback_request")
