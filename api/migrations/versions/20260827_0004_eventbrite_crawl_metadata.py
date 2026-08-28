"""Add Eventbrite crawl funnel metadata and detail cache.

Revision ID: 20260827_0004
Revises: 20260827_0003
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0004"
down_revision: str | None = "20260827_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run",
        sa.Column("categories", postgresql.ARRAY(sa.Text()), nullable=True),
        schema="ingest",
    )
    op.add_column("run", sa.Column("window_start", sa.Date()), schema="ingest")
    op.add_column("run", sa.Column("window_end", sa.Date()), schema="ingest")
    op.add_column(
        "run",
        sa.Column("listing_appearances", sa.Integer(), server_default="0"),
        schema="ingest",
    )
    op.add_column(
        "run",
        sa.Column("detail_fetched", sa.Integer(), server_default="0"),
        schema="ingest",
    )
    op.add_column(
        "run",
        sa.Column("detail_cached", sa.Integer(), server_default="0"),
        schema="ingest",
    )

    op.add_column("page_fetch", sa.Column("search_target", sa.Text()), schema="ingest")
    op.add_column("page_fetch", sa.Column("page_number", sa.Integer()), schema="ingest")

    op.create_table(
        "event_detail_cache",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source", "source_event_id"),
        schema="ingest",
    )
    op.create_index(
        "ix_event_detail_cache_expires_at",
        "event_detail_cache",
        ["expires_at"],
        schema="ingest",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_detail_cache_expires_at",
        table_name="event_detail_cache",
        schema="ingest",
    )
    op.drop_table("event_detail_cache", schema="ingest")

    op.drop_column("page_fetch", "page_number", schema="ingest")
    op.drop_column("page_fetch", "search_target", schema="ingest")

    op.drop_column("run", "detail_cached", schema="ingest")
    op.drop_column("run", "detail_fetched", schema="ingest")
    op.drop_column("run", "listing_appearances", schema="ingest")
    op.drop_column("run", "window_end", schema="ingest")
    op.drop_column("run", "window_start", schema="ingest")
    op.drop_column("run", "categories", schema="ingest")
