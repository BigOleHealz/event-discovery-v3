"""Separate canonical markets from source-native crawl locations.

Revision ID: 20260828_0006
Revises: 20260828_0005
Create Date: 2026-08-28
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0006"
down_revision: str | None = "20260828_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHILADELPHIA_MARKET_ID = uuid.UUID("8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed")


def upgrade() -> None:
    op.create_table(
        "market",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("search_bounds", Geography(geometry_type="POLYGON", srid=4326)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "country_code = upper(country_code) AND length(country_code) = 2",
            name="ck_market_country_code_iso2",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_market_slug"),
        schema="ingest",
    )
    market_table = sa.table(
        "market",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("slug", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("city", sa.Text()),
        sa.column("region", sa.Text()),
        sa.column("country_code", sa.String(length=2)),
        sa.column("timezone", sa.Text()),
        schema="ingest",
    )
    op.bulk_insert(
        market_table,
        [
            {
                "id": PHILADELPHIA_MARKET_ID,
                "slug": "philadelphia-pa",
                "name": "Philadelphia",
                "city": "Philadelphia",
                "region": "PA",
                "country_code": "US",
                "timezone": "America/New_York",
            }
        ],
    )

    op.add_column(
        "crawl_target",
        sa.Column("market_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="ingest",
    )
    op.add_column(
        "crawl_target",
        sa.Column("source_location", postgresql.JSONB(), nullable=True),
        schema="ingest",
    )
    op.create_foreign_key(
        "fk_crawl_target_market_id",
        "crawl_target",
        "market",
        ["market_id"],
        ["id"],
        source_schema="ingest",
        referent_schema="ingest",
    )
    op.execute(
        sa.text(
            """
            UPDATE ingest.crawl_target
            SET market_id = :market_id,
                source_location = jsonb_build_object(
                    'kind', 'eventbrite_slug', 'slug', location
                )
            WHERE source = 'eventbrite' AND location = 'pa--philadelphia'
            """
        ).bindparams(market_id=PHILADELPHIA_MARKET_ID)
    )
    op.alter_column("crawl_target", "market_id", nullable=False, schema="ingest")
    op.alter_column("crawl_target", "source_location", nullable=False, schema="ingest")

    op.drop_index(
        "ix_crawl_target_enabled_source_location",
        table_name="crawl_target",
        schema="ingest",
    )
    op.drop_constraint(
        "uq_crawl_target_source_location_category",
        "crawl_target",
        schema="ingest",
        type_="unique",
    )
    op.drop_column("crawl_target", "location", schema="ingest")
    op.create_unique_constraint(
        "uq_crawl_target_source_market_category_location",
        "crawl_target",
        ["source", "market_id", "category", "source_location"],
        schema="ingest",
    )
    op.create_index(
        "ix_crawl_target_enabled_source_market",
        "crawl_target",
        ["source", "market_id"],
        unique=False,
        schema="ingest",
        postgresql_where=sa.text("enabled"),
    )

    op.add_column(
        "run",
        sa.Column("market_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="ingest",
    )
    op.create_foreign_key(
        "fk_run_market_id",
        "run",
        "market",
        ["market_id"],
        ["id"],
        source_schema="ingest",
        referent_schema="ingest",
    )
    op.execute(
        sa.text(
            """
            UPDATE ingest.run
            SET market_id = :market_id,
                city_searched = 'philadelphia-pa'
            WHERE source = 'eventbrite'
              AND city_searched IN ('Philadelphia', 'pa--philadelphia')
            """
        ).bindparams(market_id=PHILADELPHIA_MARKET_ID)
    )


def downgrade() -> None:
    op.drop_constraint("fk_run_market_id", "run", schema="ingest", type_="foreignkey")
    op.drop_column("run", "market_id", schema="ingest")

    op.drop_index(
        "ix_crawl_target_enabled_source_market",
        table_name="crawl_target",
        schema="ingest",
    )
    op.drop_constraint(
        "uq_crawl_target_source_market_category_location",
        "crawl_target",
        schema="ingest",
        type_="unique",
    )
    op.add_column("crawl_target", sa.Column("location", sa.Text()), schema="ingest")
    op.execute(
        """
        UPDATE ingest.crawl_target
        SET location = COALESCE(source_location ->> 'slug', source_location::text)
        """
    )
    op.alter_column("crawl_target", "location", nullable=False, schema="ingest")
    op.create_unique_constraint(
        "uq_crawl_target_source_location_category",
        "crawl_target",
        ["source", "location", "category"],
        schema="ingest",
    )
    op.create_index(
        "ix_crawl_target_enabled_source_location",
        "crawl_target",
        ["source", "location"],
        unique=False,
        schema="ingest",
        postgresql_where=sa.text("enabled"),
    )
    op.drop_constraint(
        "fk_crawl_target_market_id",
        "crawl_target",
        schema="ingest",
        type_="foreignkey",
    )
    op.drop_column("crawl_target", "source_location", schema="ingest")
    op.drop_column("crawl_target", "market_id", schema="ingest")
    op.drop_table("market", schema="ingest")
