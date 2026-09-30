"""Run from api/: python tests/run_auth_browser.py (Docker and web dependencies required)."""

import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import create_engine, text
from testcontainers.community.postgres import PostgresContainer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contact_providers import PeopleReplay, TwilioReplay  # noqa: E402
from oauth_fixture import GoogleReplay  # noqa: E402

from app.oauth import GoogleIdentity, GoogleOAuth, OAuthAttempt, auth_config  # noqa: E402


def listener() -> socket.socket:
    result = socket.socket()
    result.bind(("127.0.0.1", 0))
    return result


def wait_ready(url: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("Web server exited before becoming ready")
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Web server did not become ready")


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")
    image = "event-discovery-postgres:16-3.4-vector-0.8.6"
    subprocess.run(["docker", "build", "-t", image, str(root / "postgres")], check=True)
    with PostgresContainer(image, driver="psycopg") as postgres, listener() as api_socket:
        database_url = postgres.get_connection_url()
        api_url = f"http://127.0.0.1:{api_socket.getsockname()[1]}"
        with listener() as web_socket:
            web_port = web_socket.getsockname()[1]
        web_url = f"http://127.0.0.1:{web_port}"
        os.environ.update(
            {
                "DATABASE_URL": database_url,
                "CORS_ALLOWED_ORIGINS": web_url,
                "PUBLIC_WEB_BASE_URL": web_url,
                "GOOGLE_CONTACTS_REDIRECT_URI": api_url + "/api/contacts/google/callback",
                "GOOGLE_PEOPLE_CONNECTIONS_URL": "https://people.example.test/connections",
                "TWILIO_ACCOUNT_SID": "ACfixture",
                "TWILIO_AUTH_TOKEN": "fixture-token",
                "TWILIO_FROM_NUMBER": "+15005550006",
                "TWILIO_MESSAGES_URL": "https://twilio.example.test/Messages.json",
                "GOOGLE_CLIENT_ID": "fixture-client",
                "GOOGLE_CLIENT_SECRET": "fixture-secret",
                "GOOGLE_OAUTH_REDIRECT_URI": api_url + "/api/auth/google/callback",
                "GOOGLE_OAUTH_AUTHORIZATION_URL": api_url + "/fixture/google",
                "GOOGLE_OAUTH_TOKEN_URL": "https://google.example.test/token",
                "GOOGLE_OAUTH_JWKS_URL": "https://google.example.test/keys",
                "SESSION_SIGNING_SECRET": secrets.token_urlsafe(48),
                "SESSION_COOKIE_SECURE": "false",
                "VITE_API_BASE_URL": api_url,
                "VITE_GOOGLE_MAPS_API_KEY": "fixture-key",
                "VITE_GOOGLE_MAPS_MAP_ID": "DEMO_MAP_ID",
                "PLAYWRIGHT_BASE_URL": web_url,
                "AUTH_E2E_API_URL": api_url,
            }
        )
        migration = Config(str(root / "api/alembic.ini"))
        migration.attributes["database_url"] = database_url
        command.upgrade(migration, "head")
        # Import after configuring CORS; replay is confined to this test-only server.
        sys.path.insert(0, str(root / "airflow"))
        from ingestion.feedback import request_event_feedback

        from app.auth import google_oauth
        from app.clock import utc_now
        from app.contacts import google_contacts
        from app.main import app
        from app.sms import twilio_sms

        people = PeopleReplay()
        sms = TwilioReplay()
        app.dependency_overrides[google_contacts] = people.provider
        app.dependency_overrides[twilio_sms] = sms.provider
        replay = GoogleReplay()
        config = auth_config()
        browser_time = datetime(2050, 9, 27, 12, tzinfo=UTC)
        app.dependency_overrides[utc_now] = lambda: browser_time
        identities = {
            "auth": replay,
            "sender": GoogleReplay(),
            "recipient": GoogleReplay(),
            "feedback": GoogleReplay(),
            "contacts": GoogleReplay(),
        }
        for name in ("sender", "recipient", "feedback", "contacts"):
            identities[name].claims.update(
                {
                    "sub": f"invite-{name}",
                    "email": f"{name}@example.com",
                    "name": name.title(),
                }
            )
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO canonical_event (id, title, starts_at, timezone, location)
                VALUES ('6b000000-0000-0000-0000-000000000001', 'Invite night', :starts, 'UTC',
                        ST_SetSRID(ST_MakePoint(-75.16,39.95),4326)::geography)
            """),
                {"starts": browser_time + timedelta(days=1)},
            )
        engine.dispose()

        class ReplayedGoogle(GoogleOAuth):
            def __init__(self, identity: str) -> None:
                super().__init__(config)
                self.replay = identities[identity]

            def exchange(self, code: str, attempt: OAuthAttempt, now: datetime) -> GoogleIdentity:
                return self.replay.provider(config, attempt, now).exchange(code, attempt, now)

        def provider(request: Request) -> GoogleOAuth:
            return ReplayedGoogle(request.cookies.get("fixture_identity", "auth"))

        app.dependency_overrides[google_oauth] = provider

        @app.get("/fixture/identity/{name}")
        def select_identity(name: str) -> RedirectResponse:
            assert name in identities
            response = RedirectResponse(config.web_url, status_code=303)
            response.set_cookie("fixture_identity", name, httponly=True, samesite="lax")
            return response

        @app.post("/fixture/finish-feedback-event")
        def finish_feedback_event() -> dict[str, int]:
            engine = create_engine(database_url)
            with engine.begin() as connection:
                connection.execute(
                    text("""
                    INSERT INTO canonical_event (id,title,starts_at,ends_at,timezone,location)
                    VALUES ('6c000000-0000-0000-0000-000000000001','Yesterday’s show',
                            :starts,:ends,'UTC',ST_SetSRID(ST_MakePoint(-75.16,39.95),4326)::geography)
                """),
                    {
                        "starts": browser_time - timedelta(days=2),
                        "ends": browser_time - timedelta(days=1),
                    },
                )
                connection.execute(
                    text("""
                    INSERT INTO attendance (id,canonical_event_id,user_id,state,source,created_at)
                    SELECT '6c000000-0000-0000-0000-000000000002',
                           '6c000000-0000-0000-0000-000000000001',id,'attending','self_rsvp',:now
                    FROM app_user WHERE google_sub='invite-feedback'
                """),
                    {"now": browser_time - timedelta(days=3)},
                )
            engine.dispose()
            return request_event_feedback(
                database_url, airflow_run_id="browser-feedback", clock=lambda: browser_time
            )

        @app.get("/fixture/google")
        def fake_google(state: str, redirect_uri: str) -> RedirectResponse:
            assert redirect_uri in {config.redirect_uri, os.environ["GOOGLE_CONTACTS_REDIRECT_URI"]}
            return RedirectResponse(
                redirect_uri
                + "?"
                + urlencode(
                    {
                        "code": "contacts-code" if "/contacts/" in redirect_uri else "fixture-code",
                        "state": state,
                    }
                ),
                status_code=303,
            )

        @app.get("/fixture/sms")
        def captured_sms() -> list[dict[str, list[str]]]:
            return sms.calls

        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [api_socket]}, daemon=True)
        thread.start()
        web: subprocess.Popen[bytes] | None = None
        try:
            subprocess.run(["npm", "run", "build"], cwd=root / "web", check=True)
            web = subprocess.Popen(
                ["npm", "run", "preview", "--", "--host", "127.0.0.1", "--port", str(web_port)],
                cwd=root / "web",
                start_new_session=True,
            )
            wait_ready(web_url, web)
            subprocess.run(
                [
                    "npm",
                    "run",
                    "test:e2e",
                    "--",
                    "auth.spec.ts",
                    "invites.spec.ts",
                    "feedback.spec.ts",
                    "contacts.spec.ts",
                ],
                cwd=root / "web",
                start_new_session=True,
                check=True,
            )
            engine = create_engine(database_url)
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM app_user WHERE google_sub='google-person-one'")
                    )
                    == 1
                )
                assert connection.scalar(text("SELECT count(*) FROM invite")) == 2
                assert connection.scalar(text("SELECT count(*) FROM attendance")) == 2
                assert (
                    connection.scalar(text("SELECT status FROM invite WHERE channel='in_app'"))
                    == "accepted"
                )
                assert connection.execute(
                    text("""
                    SELECT state,rating,feedback_text FROM attendance
                    WHERE id='6c000000-0000-0000-0000-000000000002'
                """)
                ).one() == ("attended", 4, "Loved the music.")
            engine.dispose()
            assert len(sms.calls) == 1
            assert len(people.calls) == 3
            assert len(replay.calls) == 4  # Two logins, each exchanging code and reading JWKS.
        finally:
            if web is not None:
                os.killpg(web.pid, signal.SIGTERM)
                web.wait(timeout=10)
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
