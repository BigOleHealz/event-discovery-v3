"""Mutually accepted friendships (Phase 6e)."""

import sqlalchemy as sa
from alembic import op

revision = "20261001_0020"
down_revision = "20260930_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "friendship",
        sa.Column("user_low", sa.Uuid(), sa.ForeignKey("app_user.id"), primary_key=True),
        sa.Column("user_high", sa.Uuid(), sa.ForeignKey("app_user.id"), primary_key=True),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("user_low < user_high", name="ck_friendship_order"),
        sa.CheckConstraint("requested_by IN (user_low, user_high)", name="ck_friendship_requester"),
    )
    op.create_index("ix_friendship_user_high", "friendship", ["user_high"])


def downgrade() -> None:
    op.drop_table("friendship")
