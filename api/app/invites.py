"""Postgres owns invites and attendance; the existing graph rebuild projects both."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import text

from app.auth import Clock, Config, CurrentUser, Database, require_browser_origin
from app.contact_import import normalize_phone
from app.sms import TwilioSMS, sms_config, twilio_sms

router = APIRouter(prefix="/api/invites", tags=["invites"])


class SendInvites(BaseModel):
    canonical_event_id: UUID
    emails: list[EmailStr] = Field(default_factory=list, max_length=20)
    contact_ids: list[UUID] = Field(default_factory=list, max_length=20)
    phones: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("phones")
    @classmethod
    def phone_numbers(cls, values: list[str]) -> list[str]:
        return sorted({normalize_phone(value) for value in values})

    @model_validator(mode="after")
    def recipients_limit(self) -> "SendInvites":
        if not 1 <= len(self.emails) + len(self.phones) + len(self.contact_ids) <= 20:
            raise ValueError("Choose between 1 and 20 recipients")
        return self

    message: str | None = Field(default=None, max_length=1000)

    @field_validator("emails")
    @classmethod
    def normalized_emails(cls, values: list[str]) -> list[str]:
        return sorted(set(value.casefold() for value in values))

    @field_validator("message")
    @classmethod
    def trimmed_message(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class InviteResponse(BaseModel):
    response: Literal["accept", "decline"]


class Invite(BaseModel):
    id: UUID
    canonical_event_id: UUID
    event_title: str
    starts_at: datetime
    to_user_id: UUID | None
    channel: str
    sms_state: str | None
    recipient_name: str | None
    invited_by: list[UUID]
    inviter_names: list[str]
    status: Literal["pending", "accepted", "declined"]
    message: str | None
    sent_at: datetime
    responded_at: datetime | None


# Deliberately exclude emails, phones, and Google identities from invite responses.
INVITE_SELECT = """
    SELECT i.id, i.canonical_event_id, e.title AS event_title, e.starts_at,
           i.to_user_id, coalesce(recipient.display_name, contact.display_name,
           'SMS recipient') AS recipient_name, i.invited_by, i.channel, sms.state AS sms_state,
           ARRAY(SELECT coalesce(sender.display_name, 'Someone')
                 FROM unnest(i.invited_by) WITH ORDINALITY AS names(id, position)
                 JOIN app_user sender ON sender.id = names.id ORDER BY names.position)
                 AS inviter_names,
           i.status, i.message, i.sent_at, i.responded_at
    FROM invite i JOIN canonical_event e ON e.id = i.canonical_event_id
    LEFT JOIN app_user recipient ON recipient.id = i.to_user_id
    LEFT JOIN contact ON contact.id = i.to_contact_id
    LEFT JOIN sms_delivery sms ON sms.invite_id = i.id
"""


@router.post("", dependencies=[Depends(require_browser_origin)])
def send_invites(
    payload: SendInvites,
    user: CurrentUser,
    connection: Database,
    now: Clock,
    config: Config,
    sms: Annotated[TwilioSMS, Depends(twilio_sms)],
) -> list[Invite]:
    event = connection.execute(
        text("""
        SELECT id, title FROM canonical_event
        WHERE id = :id AND archived_at IS NULL AND starts_at > :now FOR SHARE
    """),
        {"id": payload.canonical_event_id, "now": now},
    ).first()
    if event is None:
        raise HTTPException(404, "Upcoming event not found")
    recipients = (
        connection.execute(
            text("""
        SELECT id, email FROM app_user WHERE email = ANY(:emails)
        AND google_sub IS NOT NULL AND is_shadow IS FALSE ORDER BY id FOR SHARE
    """),
            {"emails": [str(email) for email in payload.emails]},
        )
        .mappings()
        .all()
    )
    if len(recipients) != len(payload.emails):
        raise HTTPException(422, "All recipients must already have an account")
    contact_ids = set(payload.contact_ids)
    # Direct phone entry becomes a private, reusable manual contact.
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('contacts:' || CAST(:id AS text), 0))"),
        {"id": user.id},
    )
    for phone in payload.phones:
        contact_ids.add(
            connection.execute(
                text("""
            INSERT INTO contact (id, owner_user_id, phone_e164, source, imported_at)
            VALUES (:id, :owner, :phone, 'manual', :now)
            ON CONFLICT (owner_user_id, (coalesce(phone_e164, lower(email))))
            DO UPDATE SET phone_e164=EXCLUDED.phone_e164 RETURNING id
        """),
                {"id": uuid4(), "owner": user.id, "phone": phone, "now": now},
            ).scalar_one()
        )
    connection.execute(text("SELECT match_contacts_to_users()"))
    contacts = (
        connection.execute(
            text("""
        SELECT id, matched_user_id, phone_e164 FROM contact
        WHERE id=ANY(:ids) AND owner_user_id=:owner ORDER BY id FOR SHARE
    """),
            {"ids": list(contact_ids), "owner": user.id},
        )
        .mappings()
        .all()
    )
    if len(contacts) != len(contact_ids):
        raise HTTPException(404, "Contact not found")
    recipient_ids = {row["id"] for row in recipients}
    recipient_ids.update(row["matched_user_id"] for row in contacts if row["matched_user_id"])
    unmatched = [row for row in contacts if not row["matched_user_id"]]
    if any(not row["phone_e164"] for row in unmatched):
        raise HTTPException(422, "Unmatched contacts need an international phone number for SMS")
    sms_settings = sms_config() if unmatched else None
    if user.id in recipient_ids:
        raise HTTPException(422, "You cannot invite yourself")
    ids: list[UUID] = []
    for recipient_id in sorted(recipient_ids):
        ids.append(
            connection.execute(
                text("""
            INSERT INTO invite (id, canonical_event_id, to_user_id, invited_by,
                                status, channel, message, sent_at)
            VALUES (:id, :event, :recipient, ARRAY[CAST(:sender AS uuid)],
                    'pending', 'in_app', :message, :now)
            ON CONFLICT (canonical_event_id, to_user_id) WHERE to_user_id IS NOT NULL
            DO UPDATE SET invited_by = CASE
                WHEN CAST(:sender AS uuid) = ANY(invite.invited_by) THEN invite.invited_by
                ELSE array_append(invite.invited_by, CAST(:sender AS uuid)) END
            RETURNING id
        """),
                {
                    "id": uuid4(),
                    "event": payload.canonical_event_id,
                    "recipient": recipient_id,
                    "sender": user.id,
                    "message": payload.message,
                    "now": now,
                },
            ).scalar_one()
        )
    sms_ids: list[UUID] = []
    for contact in unmatched:
        invite_id = connection.execute(
            text("""
            INSERT INTO invite (id, canonical_event_id, to_contact_id, invited_by,
                                status, channel, message, sent_at)
            VALUES (:id, :event, :contact, ARRAY[CAST(:sender AS uuid)],
                    'pending', 'sms', :message, :now)
            ON CONFLICT (canonical_event_id, to_contact_id) WHERE to_user_id IS NULL
            DO UPDATE SET invited_by=invite.invited_by RETURNING id
        """),
            {
                "id": uuid4(),
                "event": payload.canonical_event_id,
                "contact": contact["id"],
                "sender": user.id,
                "message": payload.message,
                "now": now,
            },
        ).scalar_one()
        body = (
            f"{(user.display_name or 'A friend')[:80]} invited you to {event.title[:200]}. "
            f"{payload.message or ''}\nView event: "
            f"{config.web_url}/?event={payload.canonical_event_id}"
        )
        connection.execute(
            text("""
            INSERT INTO sms_delivery (invite_id, phone_e164, body, state)
            VALUES (:id, :phone, :body, 'pending') ON CONFLICT (invite_id) DO NOTHING
        """),
            {"id": invite_id, "phone": contact["phone_e164"], "body": body},
        )
        ids.append(invite_id)
        sms_ids.append(invite_id)
    # Persist the invitation and outbox before any external side effects.
    connection.commit()
    if sms_settings:
        for invite_id in sms_ids:
            sms.deliver(connection, invite_id, sms_settings, now)
    rows = (
        connection.execute(
            text(INVITE_SELECT + " WHERE i.id = ANY(:ids) ORDER BY i.id"), {"ids": ids}
        )
        .mappings()
        .all()
    )
    result = [Invite.model_validate(dict(row)) for row in rows]
    connection.commit()
    return result


@router.get("/received")
def received(
    user: CurrentUser,
    connection: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Invite]:
    rows = (
        connection.execute(
            text(
                INVITE_SELECT
                + """
        WHERE i.to_user_id = :user ORDER BY i.sent_at DESC, i.id LIMIT :limit OFFSET :offset
    """
            ),
            {"user": user.id, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return [Invite.model_validate(dict(row)) for row in rows]


@router.get("/sent")
def sent(
    user: CurrentUser,
    connection: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Invite]:
    rows = (
        connection.execute(
            text(
                INVITE_SELECT
                + """
        WHERE CAST(:user AS uuid) = ANY(i.invited_by)
        ORDER BY i.sent_at DESC, i.id LIMIT :limit OFFSET :offset
    """
            ),
            {"user": user.id, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return [Invite.model_validate(dict(row)) for row in rows]


@router.post("/{invite_id}/respond", dependencies=[Depends(require_browser_origin)])
def respond(
    invite_id: UUID,
    payload: InviteResponse,
    user: CurrentUser,
    connection: Database,
    now: Clock,
) -> Invite:
    invite = (
        connection.execute(
            text("""
        SELECT i.canonical_event_id, i.status, e.starts_at, e.archived_at
        FROM invite i JOIN canonical_event e ON e.id = i.canonical_event_id
        WHERE i.id = :id AND i.to_user_id = :user FOR UPDATE OF i
    """),
            {"id": invite_id, "user": user.id},
        )
        .mappings()
        .one_or_none()
    )
    if invite is None:
        raise HTTPException(404, "Invite not found")
    status = "accepted" if payload.response == "accept" else "declined"
    if invite["status"] != status:
        if invite["starts_at"] <= now or invite["archived_at"] is not None:
            raise HTTPException(409, "This event is no longer open for responses")
        connection.execute(
            text("""
            UPDATE invite SET status = :status, responded_at = :now WHERE id = :id
        """),
            {"status": status, "now": now, "id": invite_id},
        )
        if status == "accepted":
            connection.execute(
                text("""
                INSERT INTO attendance (id, canonical_event_id, user_id, state, source, created_at)
                VALUES (:id, :event, :user, 'attending', 'invite_accept', :now)
                ON CONFLICT (canonical_event_id, user_id) DO NOTHING
            """),
                {"id": uuid4(), "event": invite["canonical_event_id"], "user": user.id, "now": now},
            )
        else:
            # Preserve independent RSVPs and historical/post-event feedback.
            connection.execute(
                text("""
                DELETE FROM attendance WHERE canonical_event_id = :event AND user_id = :user
                    AND state = 'attending' AND source = 'invite_accept'
            """),
                {"event": invite["canonical_event_id"], "user": user.id},
            )
    row = (
        connection.execute(text(INVITE_SELECT + " WHERE i.id = :id"), {"id": invite_id})
        .mappings()
        .one()
    )
    result = Invite.model_validate(dict(row))
    connection.commit()
    return result


@router.post("/{invite_id}/retry-sms", dependencies=[Depends(require_browser_origin)])
def retry_sms(
    invite_id: UUID,
    user: CurrentUser,
    connection: Database,
    now: Clock,
    sms: Annotated[TwilioSMS, Depends(twilio_sms)],
) -> Invite:
    settings = sms_config()
    row = connection.execute(
        text("""
        UPDATE sms_delivery d SET state='pending' FROM invite i, canonical_event e
        WHERE d.invite_id=i.id AND i.canonical_event_id=e.id AND i.id=:id
          AND CAST(:user AS uuid)=ANY(i.invited_by) AND e.starts_at>:now
          AND e.archived_at IS NULL AND d.state IN ('failed', 'pending') RETURNING d.invite_id
    """),
        {"id": invite_id, "user": user.id, "now": now},
    ).first()
    if row is None:
        raise HTTPException(409, "SMS cannot be safely retried")
    connection.commit()
    sms.deliver(connection, invite_id, settings, now)
    row_data = (
        connection.execute(text(INVITE_SELECT + " WHERE i.id=:id"), {"id": invite_id})
        .mappings()
        .one()
    )
    return Invite.model_validate(dict(row_data))
