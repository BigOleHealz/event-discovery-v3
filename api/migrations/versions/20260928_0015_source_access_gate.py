"""Require completed access review before enabling scraped sources (Phase 8d)."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260928_0015"
down_revision: str | None = "20260927_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Do not silently enable, disable, or approve existing operational inventory.
    # Unreviewed custom rows must be disabled before this migration can succeed.
    op.execute("""
        ALTER TABLE ingest.source_adapter ADD CONSTRAINT scraped_source_review_gate
        CHECK (NOT enabled OR fetch_method = 'api' OR coalesce(
            access_policy->>'status' = 'reviewed'
            AND min_request_interval_seconds BETWEEN 1 AND 3600, false));
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE ingest.source_adapter DROP CONSTRAINT scraped_source_review_gate")
