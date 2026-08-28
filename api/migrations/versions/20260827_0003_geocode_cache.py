"""Add an address-to-venue geocoding cache.

Revision ID: 20260827_0003
Revises: 20260827_0002
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0003"
down_revision: str | None = "20260827_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "geocode_cache",
        sa.Column("normalized_address", sa.Text(), primary_key=True),
        sa.Column("input_address", sa.Text(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), sa.ForeignKey("venue.id"), nullable=False),
        sa.Column("google_place_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        schema="ingest",
    )
    op.create_index(
        "ix_geocode_cache_google_place_id",
        "geocode_cache",
        ["google_place_id"],
        schema="ingest",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_geocode_cache_google_place_id",
        table_name="geocode_cache",
        schema="ingest",
    )
    op.drop_table("geocode_cache", schema="ingest")
