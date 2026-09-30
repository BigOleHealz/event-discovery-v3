from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.clock import utc_now
from app.database import get_connection
from app.oauth import (
    AuthConfig,
    GoogleIdentity,
    GoogleOAuth,
    auth_config,
    begin_oauth,
    clear_attempt,
    validate_attempt,
)
from app.sessions import Session, SessionStore, SignedCookieSessions

router = APIRouter(tags=["auth"])
Config = Annotated[AuthConfig, Depends(auth_config)]
Clock = Annotated[datetime, Depends(utc_now)]
Database = Annotated[Connection, Depends(get_connection)]


class User(BaseModel):
    id: UUID
    email: str | None
    display_name: str | None
    avatar_url: str | None


class GoogleCallback(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=512)


def sessions(config: Config) -> SessionStore:
    return SignedCookieSessions(
        config.secret,
        config.secure,
        config.session_ttl,
        config.refresh_ttl,
    )


Sessions = Annotated[SessionStore, Depends(sessions)]


def google_oauth(config: Config) -> GoogleOAuth:
    return GoogleOAuth(config)


def require_browser_origin(request: Request, config: Config) -> None:
    # All cookie-authenticated mutations use this dependency, including future social routes.
    # An allowlisted CORS origin alone is not CSRF protection.
    if request.headers.get("origin") != config.web_url:
        raise HTTPException(403, "Untrusted request origin")


def current_session(request: Request, store: Sessions, now: Clock) -> Session:
    return store.validate(request, now)


def current_user(
    session: Annotated[Session, Depends(current_session)],
    connection: Database,
) -> User:
    row = (
        connection.execute(
            text("""
        SELECT id, email, display_name, avatar_url FROM app_user
        WHERE id = :id AND google_sub IS NOT NULL AND is_shadow IS FALSE
    """),
            {"id": session.user_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(401, "Account is no longer available")
    return User.model_validate(dict(row))


CurrentUser = Annotated[User, Depends(current_user)]


def save_google_user(connection: Connection, identity: GoogleIdentity, now: datetime) -> User:
    try:
        row = (
            connection.execute(
                text("""
            INSERT INTO app_user (id, google_sub, email, display_name, avatar_url,
                                  is_shadow, claimed_at, created_at)
            VALUES (:id, :sub, :email, :name, :picture, false, :now, :now)
            ON CONFLICT (google_sub) DO UPDATE SET email = EXCLUDED.email,
                display_name = EXCLUDED.display_name, avatar_url = EXCLUDED.avatar_url
            RETURNING id, email, display_name, avatar_url
        """),
                {
                    "id": uuid4(),
                    "sub": identity.sub,
                    "email": identity.email.casefold(),
                    "name": identity.name,
                    "picture": identity.picture,
                    "now": now,
                },
            )
            .mappings()
            .one()
        )
        connection.execute(text("SELECT match_contacts_to_users()"))
        connection.commit()
    except IntegrityError as error:
        connection.rollback()
        # Do not silently attach a new Google subject to another account by email.
        # Claiming and merging shadow accounts belongs to 6d.1.
        raise HTTPException(409, "This email is already linked to an account") from error
    return User.model_validate(dict(row))


@router.get("/api/auth/google/start")
def start(config: Config, now: Clock) -> Response:
    response = RedirectResponse(config.web_url, status_code=303)
    response.headers["Location"] = begin_oauth(response, config, now)
    return response


def complete_google(
    payload: GoogleCallback,
    request: Request,
    response: Response,
    config: AuthConfig,
    now: datetime,
    connection: Connection,
    provider: GoogleOAuth,
    store: SessionStore,
) -> User:
    attempt = validate_attempt(request, payload.state, config, now)
    if attempt.purpose != "signin":
        raise HTTPException(400, "Invalid sign-in attempt")
    identity = provider.exchange(payload.code, attempt, now)
    user = save_google_user(connection, identity, now)
    clear_attempt(response, config)
    store.issue(response, user.id, now)
    return user


@router.post("/api/auth/google", dependencies=[Depends(require_browser_origin)])
def google_callback(
    payload: GoogleCallback,
    request: Request,
    response: Response,
    config: Config,
    now: Clock,
    connection: Database,
    store: Sessions,
    provider: Annotated[GoogleOAuth, Depends(google_oauth)],
) -> User:
    return complete_google(payload, request, response, config, now, connection, provider, store)


@router.get("/api/auth/google/callback")
def google_redirect(
    request: Request,
    config: Config,
    now: Clock,
    connection: Database,
    store: Sessions,
    provider: Annotated[GoogleOAuth, Depends(google_oauth)],
    code: str = "",
    state: str = "",
    error: str | None = None,
) -> Response:
    response = RedirectResponse(config.web_url, status_code=303)
    try:
        validate_attempt(request, state, config, now)
        if error or not code or len(code) > 4096:
            raise HTTPException(400, "Sign-in cancelled")
        complete_google(
            GoogleCallback(code=code, state=state),
            request,
            response,
            config,
            now,
            connection,
            provider,
            store,
        )
    except HTTPException:
        # Do not echo Google errors, codes, or state into the browser's URL.
        response = RedirectResponse(config.web_url + "/?auth_error=1", status_code=303)
        clear_attempt(response, config)
    return response


@router.get("/api/me")
def me(user: CurrentUser) -> User:
    return user


@router.post("/api/auth/refresh", dependencies=[Depends(require_browser_origin)])
def refresh(
    response: Response,
    user: CurrentUser,
    session: Annotated[Session, Depends(current_session)],
    store: Sessions,
    now: Clock,
) -> User:
    store.issue(response, user.id, now, refresh_until=session.refresh_until)
    return user


@router.post("/api/auth/logout", dependencies=[Depends(require_browser_origin)], status_code=204)
def logout(response: Response, store: Sessions) -> None:
    store.revoke(response)
