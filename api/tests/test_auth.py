from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from oauth_fixture import GoogleReplay
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

from app.auth import google_oauth, router
from app.clock import utc_now
from app.database import get_connection
from app.main import app
from app.oauth import (
    AuthConfig,
    GoogleIdentity,
    GoogleOAuth,
    OAuthAttempt,
    attempt_cookie,
    auth_config,
    validate_attempt,
)
from app.sessions import SignedCookieSessions

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
CONFIG = AuthConfig(
    client_id="fixture-client",
    client_secret="fixture-secret",
    redirect_uri="https://api.example.test/api/auth/google/callback",
    web_url="https://app.example.test",
    secret="test-signing-key-" * 4,
    secure=True,
    session_ttl=900,
    refresh_ttl=28800,
    authorization_url="https://google.example.test/authorize",
    token_url="https://google.example.test/token",
    jwks_url="https://google.example.test/keys",
)


AuthClient = tuple[TestClient, GoogleReplay, Connection]


@pytest.fixture
def auth_client(database_url: str) -> Iterator[AuthClient]:
    migration = Config("alembic.ini")
    migration.attributes["database_url"] = database_url
    command.upgrade(migration, "head")
    engine = create_engine(database_url)
    replay = GoogleReplay()
    with engine.connect() as connection:

        def database() -> Iterator[Connection]:
            with engine.connect() as actual:
                yield actual

        class ReplayedGoogle(GoogleOAuth):
            def exchange(self, code: str, attempt: OAuthAttempt, now: datetime) -> GoogleIdentity:
                return replay.provider(CONFIG, attempt, now).exchange(code, attempt, now)

        app.dependency_overrides[get_connection] = database
        app.dependency_overrides[auth_config] = lambda: CONFIG
        app.dependency_overrides[utc_now] = lambda: NOW
        app.dependency_overrides[google_oauth] = lambda: ReplayedGoogle(CONFIG)
        try:
            with TestClient(app, base_url="https://api.example.test") as client:
                yield client, replay, connection
        finally:
            app.dependency_overrides.clear()
            connection.rollback()
            with engine.begin() as cleanup:
                cleanup.execute(text("DELETE FROM app_user WHERE email LIKE '%@example.test'"))
    engine.dispose()


def start(client: TestClient) -> str:
    response = client.get("/api/auth/google/start", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["scope"] == ["openid email profile"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == [CONFIG.redirect_uri]
    return params["state"][0]


def login(client: TestClient) -> httpx.Response:
    return client.post(
        "/api/auth/google",
        json={"code": "fixture-code", "state": start(client)},
        headers={"Origin": CONFIG.web_url},
    )


def test_signup_repeat_login_me_refresh_logout(auth_client: AuthClient) -> None:
    client, replay, connection = auth_client
    assert client.get("/api/me").status_code == 401
    response = login(client)
    assert response.status_code == 200
    user = response.json()
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Max-Age=900" in cookie and "Domain=" not in cookie
    assert client.get("/api/me").json() == user
    assert client.get("/api/me").headers["cache-control"] == "no-store"
    assert "google_sub" not in user
    replay.claims["name"] = "Updated Friend"
    assert login(client).json() == {**user, "display_name": "Updated Friend"}
    assert (
        connection.scalar(
            text("SELECT count(*) FROM app_user WHERE google_sub = :sub"),
            {"sub": "google-person-one"},
        )
        == 1
    )
    app.dependency_overrides[utc_now] = lambda: NOW + timedelta(minutes=10)
    refreshed = client.post("/api/auth/refresh", headers={"Origin": CONFIG.web_url})
    assert refreshed.status_code == 200
    app.dependency_overrides[utc_now] = lambda: NOW + timedelta(minutes=20)
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/auth/logout", headers={"Origin": CONFIG.web_url}).status_code == 204
    assert client.get("/api/me").status_code == 401
    assert len(replay.calls) == 4


def test_cookie_works_on_another_api_instance(auth_client: AuthClient) -> None:
    client, _, connection = auth_client
    user = login(client).json()
    second = FastAPI()
    second.include_router(router)
    second.dependency_overrides = dict(app.dependency_overrides)
    with TestClient(second, base_url="https://api.example.test") as other:
        other.cookies.update(client.cookies)
        assert other.get("/api/me").json() == user
    connection.execute(text("DELETE FROM app_user WHERE id=:id"), {"id": user["id"]})
    connection.commit()
    assert client.get("/api/me").status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "another-client"},
        {"iss": "https://evil.test"},
        {"nonce": "wrong"},
        {"email_verified": False},
        {"email_verified": "true"},
        {"exp": int(NOW.timestamp())},
        {"iat": int(NOW.timestamp()) + 1},
        {"azp": "another-client"},
        {"sub": ""},
        {"nbf": int(NOW.timestamp()) + 1},
        {"aud": [CONFIG.client_id, "another-client"]},
    ],
)
def test_invalid_google_identity_creates_no_user(
    auth_client: AuthClient, changes: dict[str, object]
) -> None:
    client, replay, connection = auth_client
    replay.claims.update(changes)
    assert login(client).status_code == 401
    assert (
        connection.scalar(
            text("SELECT count(*) FROM app_user WHERE email=:email"),
            {"email": "friend@example.test"},
        )
        == 0
    )
    assert client.get("/api/me").status_code == 401


def test_wrong_signature_and_upstream_failure(auth_client: AuthClient) -> None:
    client, replay, _ = auth_client
    replay.invalid_signature = True
    assert login(client).status_code == 401
    replay.invalid_signature = False
    replay.status = 503
    assert login(client).status_code == 502
    replay.status = 400
    assert login(client).status_code == 401


def test_state_expiry_and_origin_rejected_before_exchange(auth_client: AuthClient) -> None:
    client, replay, _ = auth_client
    state = start(client)
    for origin in ("https://evil.test", "null", ""):
        assert (
            client.post(
                "/api/auth/google",
                json={"code": "fixture-code", "state": state},
                headers={"Origin": origin},
            ).status_code
            == 403
        )
    assert (
        client.post(
            "/api/auth/google",
            json={"code": "fixture-code", "state": "wrong"},
            headers={"Origin": CONFIG.web_url},
        ).status_code
        == 400
    )
    app.dependency_overrides[utc_now] = lambda: NOW + timedelta(minutes=10)
    assert (
        client.post(
            "/api/auth/google",
            json={"code": "fixture-code", "state": state},
            headers={"Origin": CONFIG.web_url},
        ).status_code
        == 400
    )
    assert replay.calls == []


def test_cross_origin_logout_refresh_are_rejected(auth_client: AuthClient) -> None:
    client, _, _ = auth_client
    login(client)
    for path in ("/api/auth/logout", "/api/auth/refresh"):
        assert client.post(path, headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.post(path).status_code == 403
    assert client.get("/api/me").status_code == 200


def test_redirect_callback_clears_binding_and_handles_denial(auth_client: AuthClient) -> None:
    client, _, _ = auth_client
    state = start(client)
    response = client.get(
        "/api/auth/google/callback",
        params={"code": "fixture-code", "state": state},
        follow_redirects=False,
    )
    assert response.headers["location"] == CONFIG.web_url
    assert attempt_cookie(CONFIG) not in client.cookies
    assert client.get("/api/me").status_code == 200
    state = start(client)
    response = client.get(
        "/api/auth/google/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert response.headers["location"] == CONFIG.web_url + "/?auth_error=1"
    assert attempt_cookie(CONFIG) not in client.cookies


def test_different_google_subject_cannot_take_existing_email(auth_client: AuthClient) -> None:
    client, replay, _ = auth_client
    first = login(client).json()
    replay.claims["sub"] = "different-subject"
    assert login(client).status_code == 409
    assert client.get("/api/me").json() == first


def test_app_user_unique_identity_constraints(auth_client: AuthClient) -> None:
    client, _, connection = auth_client
    login(client)
    for column, value in (("google_sub", "google-person-one"), ("email", "friend@example.test")):
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(f"""
                INSERT INTO app_user (id, {column}, is_shadow) VALUES (:id, :value, true)
            """),
                {"id": uuid4(), "value": value},
            )
    with pytest.raises(IntegrityError), connection.begin_nested():
        connection.execute(
            text("INSERT INTO app_user (id, is_shadow) VALUES (:id, false)"), {"id": uuid4()}
        )


def request_with_cookie(name: str, token: str) -> Request:
    return Request({"type": "http", "headers": [(b"cookie", f"{name}={token}".encode())]})


def test_signed_sessions_tampering_expiry_and_absolute_refresh_bound() -> None:
    store = SignedCookieSessions(CONFIG.secret, True, 900, 28800)
    user_id = uuid4()
    response = Response()
    store.issue(response, user_id, NOW)
    cookie = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    request = request_with_cookie(store.cookie_name, cookie)
    session = store.validate(request, NOW)
    assert session.user_id == user_id
    assert session.refresh_until == int(NOW.timestamp()) + 28800
    for candidate, when in (
        (cookie + "tampered", NOW),
        (cookie, NOW + timedelta(minutes=15)),
        (cookie, NOW - timedelta(seconds=1)),
        ("garbage", NOW),
    ):
        with pytest.raises(HTTPException):
            store.validate(request_with_cookie(store.cookie_name, candidate), when)
    other = replace(store, secret="another-secret" * 4)
    with pytest.raises(HTTPException):
        other.validate(request, NOW)
    response = Response()
    store.issue(
        response, user_id, NOW + timedelta(hours=7, minutes=59), refresh_until=session.refresh_until
    )
    assert "Max-Age=60" in response.headers["set-cookie"]
    with pytest.raises(HTTPException):
        store.issue(
            Response(), user_id, NOW + timedelta(hours=8), refresh_until=session.refresh_until
        )


def test_missing_and_tampered_oauth_cookie() -> None:
    for value in ("", "garbage"):
        with pytest.raises(HTTPException):
            validate_attempt(
                request_with_cookie(attempt_cookie(CONFIG), value), "state", CONFIG, NOW
            )


def test_auth_disabled_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SIGNING_SECRET", raising=False)
    with TestClient(app) as client:
        assert client.get("/api/auth/google/start").status_code == 503
        assert client.get("/health").status_code == 200


def test_missing_binding_and_non_ascii_state_do_not_exchange(auth_client: AuthClient) -> None:
    client, replay, _ = auth_client
    assert (
        client.post(
            "/api/auth/google",
            json={"code": "fixture-code", "state": "missing"},
            headers={"Origin": CONFIG.web_url},
        ).status_code
        == 400
    )
    start(client)
    assert (
        client.post(
            "/api/auth/google",
            json={"code": "fixture-code", "state": "\u2603"},
            headers={"Origin": CONFIG.web_url},
        ).status_code
        == 400
    )
    assert replay.calls == []


def test_me_ignores_other_user_id_and_returns_session_owner(auth_client: AuthClient) -> None:
    client, replay, _ = auth_client
    first = login(client).json()
    replay.claims.update({"sub": "second-subject", "email": "second@example.test"})
    with TestClient(app, base_url="https://api.example.test") as second:
        other = login(second).json()
        assert other["id"] != first["id"]
        assert second.get("/api/me", params={"user_id": first["id"]}).json() == other
        assert client.get("/api/me", params={"user_id": other["id"]}).json() == first


@pytest.mark.parametrize(
    "key,value",
    [
        ("SESSION_SIGNING_SECRET", "short"),
        ("SESSION_COOKIE_SECURE", "typo"),
        ("SESSION_TTL_SECONDS", "0"),
        ("SESSION_REFRESH_TTL_SECONDS", "60"),
        ("GOOGLE_OAUTH_TOKEN_URL", "http://evil.test/token"),
        ("PUBLIC_WEB_BASE_URL", "https://app.example.test/path"),
        ("GOOGLE_OAUTH_REDIRECT_URI", "https://api.example.test/callback?next=evil"),
    ],
)
def test_invalid_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    for name, setting in {
        "GOOGLE_CLIENT_ID": CONFIG.client_id,
        "GOOGLE_CLIENT_SECRET": CONFIG.client_secret,
        "GOOGLE_OAUTH_REDIRECT_URI": CONFIG.redirect_uri,
        "PUBLIC_WEB_BASE_URL": CONFIG.web_url,
        "SESSION_SIGNING_SECRET": CONFIG.secret,
        "GOOGLE_OAUTH_TOKEN_URL": CONFIG.token_url,
        "GOOGLE_OAUTH_AUTHORIZATION_URL": CONFIG.authorization_url,
        "GOOGLE_OAUTH_JWKS_URL": CONFIG.jwks_url,
        "SESSION_COOKIE_SECURE": "true",
        "SESSION_TTL_SECONDS": "900",
        "SESSION_REFRESH_TTL_SECONDS": "28800",
    }.items():
        monkeypatch.setenv(name, setting)
    monkeypatch.setenv(key, value)
    with pytest.raises(HTTPException) as exception:
        auth_config()
    assert exception.value.status_code == 503
