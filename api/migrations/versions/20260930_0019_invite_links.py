"""Hash-only share links and attributed acceptances (Phase 6d.1)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260930_0019"
down_revision: str | None = "20260928_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invite_share_link",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "canonical_event_id", sa.Uuid(), sa.ForeignKey("canonical_event.id"), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("first_opened_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("expires_at > created_at", name="ck_share_link_expiry"),
    )
    op.create_index("ix_share_link_creator", "invite_share_link", ["created_by", "created_at"])
    op.create_table(
        "invite_share_acceptance",
        sa.Column(
            "share_link_id", sa.Uuid(), sa.ForeignKey("invite_share_link.id"), primary_key=True
        ),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), primary_key=True),
        sa.Column("invite_id", sa.Uuid(), sa.ForeignKey("invite.id"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("invite_share_acceptance")
    op.drop_table("invite_share_link")
