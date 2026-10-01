"""Recipient-independent share links; explicit acceptance creates the person/event invite."""

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Connection, RowMapping, text
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import Clock, Config, CurrentUser, Database, require_browser_origin

router = APIRouter(prefix="/api/invite-links", tags=["invite links"])


class RedactInviteLinkAccessLogs:
    """Route using a private scope copy; Uvicorn logs only the redacted outer scope."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        routed_scope = scope
        if scope["type"] == "http" and scope["path"].startswith("/api/invite-links/"):
            routed_scope = dict(scope)
            scope["path"] = "/api/invite-links/[redacted]"
            scope["raw_path"] = b"/api/invite-links/[redacted]"
            scope["query_string"] = b""
        await self.app(routed_scope, receive, send)


class CreateLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_event_id: UUID
    message: str | None = Field(default=None, max_length=1000)

    @field_validator("message")
    @classmethod
    def trim(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class CreatedLink(BaseModel):
    id: UUID
    url: str
    expires_at: datetime


class Preview(BaseModel):
    canonical_event_id: UUID
    event_title: str
    starts_at: datetime
    timezone: str
    inviter_name: str
    message: str | None
    expires_at: datetime


class AcceptedBy(BaseModel):
    user_id: UUID
    display_name: str
    accepted_at: datetime


class LinkTracking(BaseModel):
    id: UUID
    canonical_event_id: UUID
    event_title: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    first_opened_at: datetime | None
    message: str | None
    acceptance_count: int
    accepted_by: list[AcceptedBy]


def token_digest(token: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        raise HTTPException(404, "Invitation link not found")
    return hashlib.sha256(token.encode()).hexdigest()


def active_link(connection: Connection, token: str, now: datetime) -> RowMapping:
    row = (
        connection.execute(
            text("""
        SELECT l.*, e.title AS event_title, e.starts_at, e.timezone, e.archived_at,
               coalesce(u.display_name, 'A friend') AS inviter_name
        FROM invite_share_link l JOIN canonical_event e ON e.id=l.canonical_event_id
        JOIN app_user u ON u.id=l.created_by
        WHERE l.token_hash=:hash FOR UPDATE OF l FOR SHARE OF e
    """),
            {"hash": token_digest(token)},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Invitation link not found")
    if row["revoked_at"] is not None or row["expires_at"] <= now:
        raise HTTPException(410, "This invitation link has expired or was revoked")
    if row["starts_at"] <= now or row["archived_at"] is not None:
        raise HTTPException(410, "This event is no longer open for invitations")
    return row


@router.post("", dependencies=[Depends(require_browser_origin)])
def create_link(
    payload: CreateLink,
    user: CurrentUser,
    connection: Database,
    config: Config,
    now: Clock,
) -> CreatedLink:
    try:
        ttl = int(os.getenv("INVITE_LINK_TTL_SECONDS", "604800"))
        if not 60 <= ttl <= 2592000:
            raise ValueError("Invalid expiry")
    except ValueError as error:
        raise HTTPException(503, "Invitation link expiry is not configured correctly") from error
    starts = connection.scalar(
        text("""
        SELECT starts_at FROM canonical_event WHERE id=:id AND archived_at IS NULL
        AND starts_at>:now FOR SHARE
    """),
        {"id": payload.canonical_event_id, "now": now},
    )
    if starts is None:
        raise HTTPException(404, "Upcoming event not found")
    token = secrets.token_urlsafe(32)
    link_id = uuid4()
    expires = min(starts, now + timedelta(seconds=ttl))
    connection.execute(
        text("""
        INSERT INTO invite_share_link
            (id,canonical_event_id,created_by,token_hash,message,created_at,expires_at)
        VALUES (:id,:event,:user,:hash,:message,:now,:expires)
    """),
        {
            "id": link_id,
            "event": payload.canonical_event_id,
            "user": user.id,
            "hash": token_digest(token),
            "message": payload.message,
            "now": now,
            "expires": expires,
        },
    )
    connection.commit()
    # Fragments are not sent to the web server or included in HTTP referrers.
    return CreatedLink(id=link_id, url=f"{config.web_url}/#invite={token}", expires_at=expires)


@router.get("")
def list_links(
    user: CurrentUser,
    connection: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LinkTracking]:
    rows = (
        connection.execute(
            text("""
        SELECT l.id,l.canonical_event_id,e.title AS event_title,l.created_at,l.expires_at,
               l.revoked_at,l.first_opened_at,l.message,
               (SELECT count(*) FROM invite_share_acceptance a WHERE a.share_link_id=l.id)
               AS acceptance_count
        FROM invite_share_link l JOIN canonical_event e ON e.id=l.canonical_event_id
        WHERE l.created_by=:user ORDER BY l.created_at DESC,l.id LIMIT :limit OFFSET :offset
    """),
            {"user": user.id, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    result = []
    for row in rows:
        accepted = (
            connection.execute(
                text("""
            SELECT a.user_id,coalesce(u.display_name,'Someone') AS display_name,a.accepted_at
            FROM invite_share_acceptance a JOIN app_user u ON u.id=a.user_id
            WHERE a.share_link_id=:id ORDER BY a.accepted_at,a.user_id LIMIT 100
        """),
                {"id": row["id"]},
            )
            .mappings()
            .all()
        )
        result.append(LinkTracking.model_validate({**row, "accepted_by": accepted}))
    return result


@router.post("/{link_id}/revoke", dependencies=[Depends(require_browser_origin)], status_code=204)
def revoke_link(link_id: UUID, user: CurrentUser, connection: Database, now: Clock) -> None:
    found = connection.scalar(
        text("""
        UPDATE invite_share_link SET revoked_at=coalesce(revoked_at,:now)
        WHERE id=:id AND created_by=:user RETURNING id
    """),
        {"id": link_id, "user": user.id, "now": now},
    )
    if found is None:
        raise HTTPException(404, "Invitation link not found")
    connection.commit()


@router.get("/{token}")
def preview_link(token: str, connection: Database, now: Clock) -> Preview:
    link = active_link(connection, token, now)
    connection.execute(
        text("""
        UPDATE invite_share_link SET first_opened_at=coalesce(first_opened_at,:now) WHERE id=:id
    """),
        {"id": link["id"], "now": now},
    )
    result = Preview.model_validate(dict(link))
    connection.commit()
    return result


class Acceptance(BaseModel):
    invite_id: UUID
    status: str = "accepted"


@router.post("/{token}/accept", dependencies=[Depends(require_browser_origin)])
def accept_link(token: str, user: CurrentUser, connection: Database, now: Clock) -> Acceptance:
    link = active_link(connection, token, now)
    if link["created_by"] == user.id:
        raise HTTPException(422, "You cannot accept your own invitation")
    invite_id = connection.scalar(
        text("""
        INSERT INTO invite (id,canonical_event_id,to_user_id,invited_by,status,channel,
                            message,sent_at,responded_at)
        VALUES (:id,:event,:user,ARRAY[CAST(:creator AS uuid)],'accepted','share_link',
                :message,:now,:now)
        ON CONFLICT (canonical_event_id,to_user_id) WHERE to_user_id IS NOT NULL
        DO UPDATE SET
            invited_by=CASE WHEN CAST(:creator AS uuid)=ANY(invite.invited_by)
                THEN invite.invited_by
                ELSE array_append(invite.invited_by,CAST(:creator AS uuid)) END,
            status='accepted',
            responded_at=CASE WHEN invite.status='accepted' THEN invite.responded_at ELSE :now END
        RETURNING id
    """),
        {
            "id": uuid4(),
            "event": link["canonical_event_id"],
            "user": user.id,
            "creator": link["created_by"],
            "message": link["message"],
            "now": now,
        },
    )
    connection.execute(
        text("""
        INSERT INTO attendance (id,canonical_event_id,user_id,state,source,created_at)
        VALUES (:id,:event,:user,'attending','invite_accept',:now)
        ON CONFLICT (canonical_event_id,user_id) DO NOTHING
    """),
        {"id": uuid4(), "event": link["canonical_event_id"], "user": user.id, "now": now},
    )
    connection.execute(
        text("""
        INSERT INTO invite_share_acceptance (share_link_id,user_id,invite_id,accepted_at)
        VALUES (:link,:user,:invite,:now) ON CONFLICT (share_link_id,user_id) DO NOTHING
    """),
        {"link": link["id"], "user": user.id, "invite": invite_id, "now": now},
    )
    connection.commit()
    return Acceptance(invite_id=invite_id)
