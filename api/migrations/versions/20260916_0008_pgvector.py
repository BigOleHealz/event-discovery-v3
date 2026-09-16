"""Store dedup embeddings in Postgres and link recurring occurrences.

Revision ID: 20260916_0008
Revises: 20260828_0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260916_0008"
down_revision: str | None = "20260828_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE source_listing ADD COLUMN embedding vector(1536)")
    op.execute(
        "CREATE INDEX ix_source_listing_embedding ON source_listing "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("ALTER TABLE canonical_event ADD COLUMN recurrence_group_id UUID")


def downgrade() -> None:
    op.drop_column("canonical_event", "recurrence_group_id")
    op.drop_index("ix_source_listing_embedding", table_name="source_listing")
    op.drop_column("source_listing", "embedding")
    # The extension may predate this migration and be used by other tables.
