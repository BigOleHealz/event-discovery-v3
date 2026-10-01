"""Owner-scoped contact imports, shared matching, and durable SMS attempts (6d)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0018"
down_revision: str | None = "20260928_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_contact_owner_identity",
        "contact",
        ["owner_user_id", sa.text("coalesce(phone_e164, lower(email))")],
        unique=True,
    )
    op.create_index(
        "uq_invite_event_contact",
        "invite",
        ["canonical_event_id", "to_contact_id"],
        unique=True,
        postgresql_where=sa.text("to_user_id IS NULL"),
    )
    op.create_table(
        "sms_delivery",
        sa.Column(
            "invite_id", sa.Uuid(), sa.ForeignKey("invite.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("provider_sid", sa.Text(), unique=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('pending', 'sending', 'submitted', 'failed', 'unknown')",
            name="ck_sms_delivery_state",
        ),
    )
    # Imports, signups, and the scheduled repair job use the same canonical rule.
    op.execute("""
        CREATE FUNCTION match_contacts_to_users() RETURNS integer LANGUAGE plpgsql AS $$
        DECLARE changed integer;
        BEGIN
          UPDATE contact c SET matched_user_id = matches.user_id
          FROM (
            SELECT c.id, coalesce(
              (SELECT u.id FROM app_user u WHERE u.phone_e164 = c.phone_e164
                AND u.google_sub IS NOT NULL AND NOT u.is_shadow),
              (SELECT u.id FROM app_user u WHERE u.email = c.email
                AND u.google_sub IS NOT NULL AND NOT u.is_shadow)
            ) AS user_id FROM contact c
          ) matches
          WHERE c.id = matches.id AND c.matched_user_id IS DISTINCT FROM matches.user_id;
          GET DIAGNOSTICS changed = ROW_COUNT;
          RETURN changed;
        END $$
    """)
    op.drop_constraint("ck_run_market_scope", "run", schema="ingest", type_="check")
    op.create_check_constraint(
        "ck_run_market_scope",
        "run",
        "market_id IS NOT NULL OR COALESCE(airflow_dag_id IN "
        "('project_to_neo4j', 'request_event_feedback', 'match_contacts_to_users'), false)",
        schema="ingest",
    )


def downgrade() -> None:
    op.drop_constraint("ck_run_market_scope", "run", schema="ingest", type_="check")
    op.create_check_constraint(
        "ck_run_market_scope",
        "run",
        "market_id IS NOT NULL OR COALESCE(airflow_dag_id IN "
        "('project_to_neo4j', 'request_event_feedback'), false)",
        schema="ingest",
    )
    op.execute("DROP FUNCTION match_contacts_to_users()")
    op.drop_table("sms_delivery")
    op.drop_index("uq_invite_event_contact", table_name="invite")
    op.drop_index("uq_contact_owner_identity", table_name="contact")
