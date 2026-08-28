"""Add database-backed crawl targets.

Revision ID: 20260828_0005
Revises: 20260827_0004
Create Date: 2026-08-28
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0005"
down_revision: str | None = "20260827_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHILADELPHIA_SCIENCE_TARGET_ID = uuid.UUID("4c33ed98-a96b-4d72-946f-5bd923db9506")
PHILADELPHIA_FOOD_TARGET_ID = uuid.UUID("a8ddad4f-d5af-4f24-a8a6-99e10aa3d76f")


def upgrade() -> None:
    op.create_table(
        "crawl_target",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("window_days", sa.SmallInteger(), nullable=False, server_default="5"),
        sa.Column("page_cap", sa.SmallInteger(), nullable=False, server_default="20"),
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
        sa.CheckConstraint("window_days > 0", name="ck_crawl_target_window_days_positive"),
        sa.CheckConstraint("page_cap > 0", name="ck_crawl_target_page_cap_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source", "location", "category", name="uq_crawl_target_source_location_category"
        ),
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

    target_table = sa.table(
        "crawl_target",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("source", sa.Text()),
        sa.column("location", sa.Text()),
        sa.column("category", sa.Text()),
        sa.column("enabled", sa.Boolean()),
        sa.column("window_days", sa.SmallInteger()),
        sa.column("page_cap", sa.SmallInteger()),
        schema="ingest",
    )
    op.bulk_insert(
        target_table,
        [
            {
                "id": PHILADELPHIA_SCIENCE_TARGET_ID,
                "source": "eventbrite",
                "location": "pa--philadelphia",
                "category": "science-and-tech",
                "enabled": True,
                "window_days": 5,
                "page_cap": 20,
            },
            {
                "id": PHILADELPHIA_FOOD_TARGET_ID,
                "source": "eventbrite",
                "location": "pa--philadelphia",
                "category": "food-and-drink",
                "enabled": True,
                "window_days": 5,
                "page_cap": 20,
            },
        ],
    )

    op.add_column(
        "page_fetch",
        sa.Column("crawl_target_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="ingest",
    )
    op.create_foreign_key(
        "fk_page_fetch_crawl_target_id",
        "page_fetch",
        "crawl_target",
        ["crawl_target_id"],
        ["id"],
        source_schema="ingest",
        referent_schema="ingest",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_page_fetch_crawl_target_id",
        "page_fetch",
        schema="ingest",
        type_="foreignkey",
    )
    op.drop_column("page_fetch", "crawl_target_id", schema="ingest")
    op.drop_index(
        "ix_crawl_target_enabled_source_location",
        table_name="crawl_target",
        schema="ingest",
    )
    op.drop_table("crawl_target", schema="ingest")
