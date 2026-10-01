"""Cookie storage is confined to this interface; no process-local session state."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from fastapi import HTTPException, Request, Response
from itsdangerous import BadData, URLSafeSerializer
from pydantic import BaseModel, ValidationError


class Session(BaseModel):
    user_id: UUID
    issued_at: int
    expires_at: int
    refresh_until: int


class SessionStore(Protocol):
    def issue(
        self,
        response: Response,
        user_id: UUID,
        now: datetime,
        *,
        refresh_until: int | None = None,
    ) -> None: ...

    def validate(self, request: Request, now: datetime) -> Session: ...

    def revoke(self, response: Response) -> None: ...


@dataclass(frozen=True)
class SignedCookieSessions:
    secret: str
    secure: bool
    ttl_seconds: int
    refresh_seconds: int

    @property
    def cookie_name(self) -> str:
        return "__Host-event_session" if self.secure else "event_session"

    @property
    def signer(self) -> URLSafeSerializer:
        return URLSafeSerializer(self.secret, salt="event-discovery-session-v1")

    def issue(
        self,
        response: Response,
        user_id: UUID,
        now: datetime,
        *,
        refresh_until: int | None = None,
    ) -> None:
        issued = int(now.timestamp())
        deadline = refresh_until if refresh_until is not None else issued + self.refresh_seconds
        expires = min(issued + self.ttl_seconds, deadline)
        if expires <= issued:
            raise HTTPException(401, "Sign in again")
        session = Session(
            user_id=user_id,
            issued_at=issued,
            expires_at=expires,
            refresh_until=deadline,
        )
        response.set_cookie(
            self.cookie_name,
            self.signer.dumps(session.model_dump(mode="json")),
            max_age=expires - issued,
            httponly=True,
            secure=self.secure,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"

    def validate(self, request: Request, now: datetime) -> Session:
        token = request.cookies.get(self.cookie_name)
        if not token:
            raise HTTPException(401, "Sign in required")
        try:
            session = Session.model_validate(self.signer.loads(token))
        except (BadData, ValidationError) as error:
            raise HTTPException(401, "Invalid session") from error
        timestamp = int(now.timestamp())
        if not session.issued_at <= timestamp < session.expires_at <= session.refresh_until:
            raise HTTPException(401, "Session expired")
        return session

    def revoke(self, response: Response) -> None:
        # Signed cookies cannot revoke a stolen copy. Short expiry bounds that window.
        response.delete_cookie(
            self.cookie_name,
            path="/",
            secure=self.secure,
            httponly=True,
            samesite="lax",
        )
        response.headers["Cache-Control"] = "no-store"
