"""Persist retry-safe dedup outcomes; review UI is a later sub-phase.

Revision ID: 20260916_0009
Revises: 20260916_0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260916_0009"
down_revision: str | None = "20260916_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE source_listing
            ADD COLUMN dedup_state TEXT NOT NULL DEFAULT 'pending'
                CHECK (dedup_state IN ('pending', 'exact', 'same', 'review', 'distinct')),
            ADD COLUMN dedup_match_id UUID REFERENCES source_listing(id) ON DELETE SET NULL,
            ADD COLUMN dedup_similarity DOUBLE PRECISION,
            ADD COLUMN dedup_checked_at TIMESTAMPTZ;
        CREATE INDEX ix_source_listing_dedup_pending ON source_listing (id)
            WHERE dedup_state = 'pending';

        -- Before 4d, shared canonicals came from 4c's exact-match shortcut.
        -- Keep one representative eligible for embedding; redundant listings
        -- must not start paying for embeddings merely because we upgraded.
        WITH representatives AS (
            SELECT id, row_number() OVER (
                PARTITION BY canonical_event_id
                ORDER BY (embedding IS NULL), first_seen_at, id
            ) AS ordinal
            FROM source_listing
            WHERE canonical_event_id IS NOT NULL
        )
        UPDATE source_listing SET dedup_state = 'exact'
        FROM representatives
        WHERE source_listing.id = representatives.id AND ordinal > 1;
    """)


def downgrade() -> None:
    op.drop_index("ix_source_listing_dedup_pending", table_name="source_listing")
    for column in ("dedup_checked_at", "dedup_similarity", "dedup_match_id", "dedup_state"):
        op.drop_column("source_listing", column)
