"""Seed Meetup targets and finish the canonical-market run migration.

Revision ID: 20260828_0007
Revises: 20260828_0006
Create Date: 2026-08-28
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0007"
down_revision: str | None = "20260828_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHILADELPHIA_MARKET_ID = uuid.UUID("8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed")
MEETUP_TECHNOLOGY_TARGET_ID = uuid.UUID("b8d299ce-bf03-4bc8-aa35-16f25fd95d34")
MEETUP_SOCIAL_TARGET_ID = uuid.UUID("5e83bd87-ce71-48e7-b5d2-126bd69fe17a")
MEETUP_PHILADELPHIA_LOCATION = {
    "kind": "meetup_geo_radius",
    "lat": 39.9526,
    "lon": -75.1652,
    "radius_miles": 25,
}


def upgrade() -> None:
    crawl_target = sa.table(
        "crawl_target",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("source", sa.Text()),
        sa.column("market_id", postgresql.UUID(as_uuid=True)),
        sa.column("source_location", postgresql.JSONB()),
        sa.column("category", sa.Text()),
        sa.column("enabled", sa.Boolean()),
        sa.column("window_days", sa.SmallInteger()),
        sa.column("page_cap", sa.SmallInteger()),
        schema="ingest",
    )
    op.bulk_insert(
        crawl_target,
        [
            {
                "id": MEETUP_TECHNOLOGY_TARGET_ID,
                "source": "meetup",
                "market_id": PHILADELPHIA_MARKET_ID,
                "source_location": MEETUP_PHILADELPHIA_LOCATION,
                "category": "546",
                "enabled": True,
                "window_days": 5,
                "page_cap": 20,
            },
            {
                "id": MEETUP_SOCIAL_TARGET_ID,
                "source": "meetup",
                "market_id": PHILADELPHIA_MARKET_ID,
                "source_location": MEETUP_PHILADELPHIA_LOCATION,
                "category": "652",
                "enabled": True,
                "window_days": 5,
                "page_cap": 20,
            },
        ],
    )

    op.alter_column("run", "market_id", nullable=False, schema="ingest")
    op.drop_column("run", "city_searched", schema="ingest")


def downgrade() -> None:
    op.add_column("run", sa.Column("city_searched", sa.Text()), schema="ingest")
    op.execute(
        """
        UPDATE ingest.run AS run
        SET city_searched = market.slug
        FROM ingest.market AS market
        WHERE market.id = run.market_id
        """
    )
    op.alter_column("run", "city_searched", nullable=False, schema="ingest")
    op.alter_column("run", "market_id", nullable=True, schema="ingest")
    op.execute(
        sa.text(
            """
            DELETE FROM ingest.crawl_target
            WHERE id IN (:technology_id, :social_id)
            """
        ).bindparams(
            technology_id=MEETUP_TECHNOLOGY_TARGET_ID,
            social_id=MEETUP_SOCIAL_TARGET_ID,
        )
    )
