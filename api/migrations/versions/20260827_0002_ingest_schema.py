"""Create the ingestion metadata schema.

Revision ID: 20260827_0002
Revises: 20260827_0001
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0002"
down_revision: str | None = "20260827_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA ingest")

    op.create_table(
        "run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("city_searched", sa.Text(), nullable=False),
        sa.Column("search_bounds", Geography(geometry_type="POLYGON", srid=4326)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("events_found", sa.Integer(), server_default="0"),
        sa.Column("events_new", sa.Integer(), server_default="0"),
        sa.Column("events_updated", sa.Integer(), server_default="0"),
        sa.Column("events_deduped", sa.Integer(), server_default="0"),
        sa.Column("events_rejected_online", sa.Integer(), server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("airflow_dag_id", sa.Text()),
        sa.Column("airflow_run_id", sa.Text()),
        schema="ingest",
    )

    op.create_table(
        "page_fetch",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("ingest.run.id")),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("fetch_method", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("bytes", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="ingest",
    )

    op.create_table(
        "rejected_listing",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("ingest.run.id")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB()),
        sa.Column("rejected_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        schema="ingest",
    )


def downgrade() -> None:
    op.drop_table("rejected_listing", schema="ingest")
    op.drop_table("page_fetch", schema="ingest")
    op.drop_table("run", schema="ingest")
    op.execute("DROP SCHEMA ingest")
