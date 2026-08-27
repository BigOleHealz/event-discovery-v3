"""Create the canonical schema in specification dependency order.

Revision ID: 20260827_0001
Revises:
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # PROJECT_PLAN.md §3.1 is dependency order. Keep table creation in this order.
    op.create_table(
        "venue",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text()),
        sa.Column("formatted_address", sa.Text()),
        sa.Column("google_place_id", sa.Text(), unique=True),
        sa.Column("location", Geography(geometry_type="POINT", srid=4326)),
        sa.Column("city", sa.Text()),
        sa.Column("region", sa.Text()),
        sa.Column("country", sa.Text()),
    )

    op.create_table(
        "canonical_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("venue_id", sa.Uuid(), sa.ForeignKey("venue.id")),
        sa.Column("location", Geography(geometry_type="POINT", srid=4326), nullable=False),
        sa.Column("primary_category", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_canonical_event_location", "canonical_event", ["location"], postgresql_using="gist"
    )
    op.create_index("ix_canonical_event_starts_at", "canonical_event", ["starts_at"])

    op.create_table(
        "source_listing",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("canonical_event_id", sa.Uuid(), sa.ForeignKey("canonical_event.id")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("registration_url", sa.Text()),
        sa.Column("price_min", sa.Numeric()),
        sa.Column("price_max", sa.Numeric()),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("ingestion_run_id", sa.Uuid(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("source", "source_event_id", name="uq_source_listing_source_event"),
    )

    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("google_sub", sa.Text(), unique=True),
        sa.Column("email", sa.Text(), unique=True),
        sa.Column("phone_e164", sa.Text(), unique=True),
        sa.Column("display_name", sa.Text()),
        sa.Column("avatar_url", sa.Text()),
        sa.Column("is_shadow", sa.Boolean(), server_default=sa.false()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint("google_sub IS NOT NULL OR is_shadow", name="ck_app_user_identity"),
    )

    op.create_table(
        "contact",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("app_user.id")),
        sa.Column("display_name", sa.Text()),
        sa.Column("phone_e164", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("matched_user_id", sa.Uuid(), sa.ForeignKey("app_user.id")),
        sa.Column("source", sa.Text()),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_contact_owner_user_id", "contact", ["owner_user_id"])

    op.create_table(
        "invite",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("canonical_event_id", sa.Uuid(), sa.ForeignKey("canonical_event.id")),
        sa.Column("to_user_id", sa.Uuid(), sa.ForeignKey("app_user.id")),
        sa.Column("to_contact_id", sa.Uuid(), sa.ForeignKey("contact.id")),
        sa.Column("invited_by", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending"),
        sa.Column("channel", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "to_user_id IS NOT NULL OR to_contact_id IS NOT NULL",
            name="ck_invite_recipient",
        ),
    )
    op.create_index(
        "uq_invite_event_user",
        "invite",
        ["canonical_event_id", "to_user_id"],
        unique=True,
        postgresql_where=sa.text("to_user_id IS NOT NULL"),
    )

    op.create_table(
        "attendance",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("canonical_event_id", sa.Uuid(), sa.ForeignKey("canonical_event.id")),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id")),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("source", sa.Text()),
        sa.Column("rating", sa.SmallInteger()),
        sa.Column("feedback_text", sa.Text()),
        sa.Column("feedback_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("canonical_event_id", "user_id", name="uq_attendance_event_user"),
    )

    op.create_table(
        "saved_search",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("bounds", Geography(geometry_type="POLYGON", srid=4326), nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.Text())),
        sa.Column("day_of_week", postgresql.ARRAY(sa.SmallInteger())),
        sa.Column("time_of_day_start", sa.Time()),
        sa.Column("time_of_day_end", sa.Time()),
        sa.Column("notify_channel", sa.Text(), server_default="email"),
        sa.Column("notify_frequency", sa.Text(), server_default="daily"),
        sa.Column("last_notified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "push_subscription",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id")),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("keys", postgresql.JSONB()),
        sa.Column("platform", sa.Text()),
        sa.Column("home_location", Geography(geometry_type="POINT", srid=4326)),
        sa.Column("radius_meters", sa.Integer(), server_default="25000"),
        sa.Column("quiet_hours_start", sa.Time()),
        sa.Column("quiet_hours_end", sa.Time()),
        sa.Column("max_per_week", sa.SmallInteger(), server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id")),
        sa.Column("canonical_event_id", sa.Uuid(), sa.ForeignKey("canonical_event.id")),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "user_id",
            "canonical_event_id",
            "trigger",
            name="uq_notification_log_delivery",
        ),
    )

    op.create_table(
        "saved_search_hit",
        sa.Column("saved_search_id", sa.Uuid(), sa.ForeignKey("saved_search.id"), primary_key=True),
        sa.Column(
            "canonical_event_id",
            sa.Uuid(),
            sa.ForeignKey("canonical_event.id"),
            primary_key=True,
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("saved_search_hit")
    op.drop_table("notification_log")
    op.drop_table("push_subscription")
    op.drop_table("saved_search")
    op.drop_table("attendance")
    op.drop_index("uq_invite_event_user", table_name="invite")
    op.drop_table("invite")
    op.drop_index("ix_contact_owner_user_id", table_name="contact")
    op.drop_table("contact")
    op.drop_table("app_user")
    op.drop_table("source_listing")
    op.drop_index("ix_canonical_event_starts_at", table_name="canonical_event")
    op.drop_index(
        "ix_canonical_event_location", table_name="canonical_event", postgresql_using="gist"
    )
    op.drop_table("canonical_event")
    op.drop_table("venue")
