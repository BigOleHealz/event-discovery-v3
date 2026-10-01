import asyncio
import hashlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from ingestion.graph import GraphConfig, project_to_neo4j
from neo4j import GraphDatabase
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from starlette.types import Receive, Scope, Send
from test_auth import CONFIG, NOW
from test_invites import InviteData, invite_data  # noqa: F401
from test_migrations import migration_config, table_names

from app.invite_links import RedactInviteLinkAccessLogs
from app.main import app


@pytest.fixture
def links_data(invite_data: InviteData, monkeypatch: pytest.MonkeyPatch) -> Iterator[InviteData]:  # noqa: F811
    monkeypatch.setenv("INVITE_LINK_TTL_SECONDS", "604800")
    yield invite_data
    with invite_data.engine.begin() as conn:
        conn.execute(
            text("""
            DELETE FROM invite_share_acceptance WHERE share_link_id IN
            (SELECT id FROM invite_share_link WHERE canonical_event_id=:event)
        """),
            {"event": invite_data.event},
        )
        conn.execute(
            text("DELETE FROM invite_share_link WHERE canonical_event_id=:event"),
            {"event": invite_data.event},
        )


def create(data: InviteData, sender: int = 0) -> dict[str, str]:
    with data.client(sender) as client:
        response = client.post(
            "/api/invite-links",
            json={
                "canonical_event_id": str(data.event),
                "message": "Join us!",
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        return dict(response.json())


def token(link: dict[str, str]) -> str:
    return link["url"].split("#invite=")[1]


def accept(data: InviteData, secret: str, user: int = 2) -> dict[str, str]:
    with data.client(user) as client:
        response = client.post(f"/api/invite-links/{secret}/accept")
        assert response.status_code == 200, response.text
        return dict(response.json())


def test_creation_preview_hash_storage_and_owner_privacy(links_data: InviteData) -> None:
    data = links_data
    link = create(data)
    secret = token(link)
    assert len(secret) == 43
    assert link["expires_at"] == (NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    with TestClient(app) as anonymous:
        for _ in range(2):
            response = anonymous.get(f"/api/invite-links/{secret}")
            assert response.status_code == 200, response.text
            assert response.headers["referrer-policy"] == "no-referrer"
            assert response.headers["cache-control"] == "no-store"
            assert set(response.json()) == {
                "canonical_event_id",
                "event_title",
                "starts_at",
                "timezone",
                "inviter_name",
                "message",
                "expires_at",
            }
        assert (
            anonymous.post(
                f"/api/invite-links/{secret}/accept", headers={"Origin": CONFIG.web_url}
            ).status_code
            == 401
        )
        assert anonymous.get("/api/invite-links").status_code == 401
        assert (
            anonymous.post(
                "/api/invite-links",
                headers={"Origin": CONFIG.web_url},
                json={"canonical_event_id": str(data.event)},
            ).status_code
            == 401
        )
    with data.client(0) as owner, data.client(1) as other:
        tracking = owner.get("/api/invite-links").json()
        assert len(tracking) == 1
        assert tracking[0]["first_opened_at"] == NOW.isoformat().replace("+00:00", "Z")
        assert tracking[0]["acceptance_count"] == 0 and tracking[0]["accepted_by"] == []
        assert secret not in str(tracking) and "token_hash" not in str(tracking)
        assert (
            other.get("/api/invite-links", params={"created_by": str(data.users[0])}).json() == []
        )
        assert other.post(f"/api/invite-links/{link['id']}/revoke").status_code == 404
        assert owner.post(f"/api/invite-links/{secret}/accept").status_code == 422
        assert (
            owner.post(
                f"/api/invite-links/{link['id']}/revoke", headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
    with data.engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT token_hash FROM invite_share_link WHERE id=:id"), {"id": link["id"]}
            )
            == hashlib.sha256(secret.encode()).hexdigest()
        )
        assert conn.scalar(text("SELECT count(*) FROM invite")) == 0
        assert conn.scalar(text("SELECT count(*) FROM attendance")) == 0
        assert (
            conn.scalar(
                text("SELECT count(*) FROM app_user WHERE id=ANY(:users)"), {"users": data.users}
            )
            == 4
        )


def test_concurrent_creators_forwarding_and_existing_invite(links_data: InviteData) -> None:
    data = links_data
    original = data.send(0)
    links = [create(data, creator) for creator in (0, 1)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        accepted = list(pool.map(lambda link: accept(data, token(link)), links * 2))
    assert {row["invite_id"] for row in accepted} == {original["id"]}
    accept(data, token(links[0]), 3)
    with data.client(2) as recipient:
        rows = recipient.get("/api/invites/received").json()
        assert len(rows) == 1 and rows[0]["status"] == "accepted"
        assert set(rows[0]["invited_by"]) == {str(data.users[0]), str(data.users[1])}
        assert rows[0]["channel"] == "in_app"
        assert (
            recipient.post(
                f"/api/invite-links/{token(links[0])}/accept",
                headers={"Origin": "https://evil.test"},
            ).status_code
            == 403
        )
    with data.client(0) as owner:
        row = owner.get("/api/invite-links").json()[0]
        assert row["acceptance_count"] == 2
        assert {person["user_id"] for person in row["accepted_by"]} == {
            str(data.users[2]),
            str(data.users[3]),
        }
        assert "email" not in str(row) and "phone" not in str(row)
    with data.engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM attendance WHERE canonical_event_id=:event"),
                {"event": data.event},
            )
            == 2
        )
        assert conn.scalar(text("SELECT count(*) FROM invite_share_acceptance")) == 3
        assert conn.scalar(text("SELECT count(*) FROM sms_delivery")) == 0
    with pytest.raises(IntegrityError), data.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO invite(id,canonical_event_id,to_user_id,invited_by)
            VALUES (:id,:event,:user,ARRAY[CAST(:sender AS uuid)])
        """),
            {"id": uuid4(), "event": data.event, "user": data.users[2], "sender": data.users[0]},
        )
    with pytest.raises(IntegrityError), data.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO invite_share_acceptance SELECT * FROM invite_share_acceptance LIMIT 1
        """)
        )


@pytest.mark.parametrize("state", ["expired", "revoked", "past", "archived", "invalid"])
def test_unavailable_links_never_accept(links_data: InviteData, state: str) -> None:
    data = links_data
    link = create(data)
    secret = token(link)
    with data.engine.begin() as conn:
        if state == "expired":
            conn.execute(
                text("""
                UPDATE invite_share_link SET created_at=:before,expires_at=:now WHERE id=:id
            """),
                {"before": NOW - timedelta(days=1), "now": NOW, "id": link["id"]},
            )
        elif state == "past":
            conn.execute(
                text("UPDATE canonical_event SET starts_at=:now WHERE id=:id"),
                {"id": data.event, "now": NOW},
            )
        elif state == "archived":
            conn.execute(
                text("UPDATE canonical_event SET archived_at=:now WHERE id=:id"),
                {"id": data.event, "now": NOW},
            )
    with data.client(0) as owner, data.client(2) as recipient:
        if state == "revoked":
            for _ in range(2):
                assert owner.post(f"/api/invite-links/{link['id']}/revoke").status_code == 204
        if state == "invalid":
            secret = "z" * 43
        expected = 404 if state == "invalid" else 410
        assert recipient.get(f"/api/invite-links/{secret}").status_code == expected
        assert recipient.post(f"/api/invite-links/{secret}/accept").status_code == expected
        assert recipient.get("/api/invites/received").json() == []
    with data.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM invite_share_acceptance")) == 0
        assert conn.scalar(text("SELECT count(*) FROM attendance")) == 0


def test_acceptance_rolls_back_every_write(links_data: InviteData) -> None:
    data = links_data
    link = create(data)
    with data.engine.begin() as conn:
        conn.execute(text("ALTER TABLE attendance ADD CONSTRAINT fixture_block CHECK (false)"))
    try:
        with data.client(2) as recipient:
            assert recipient.post(f"/api/invite-links/{token(link)}/accept").status_code == 500
        with data.engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM invite")) == 0
            assert conn.scalar(text("SELECT count(*) FROM invite_share_acceptance")) == 0
    finally:
        with data.engine.begin() as conn:
            conn.execute(text("ALTER TABLE attendance DROP CONSTRAINT fixture_block"))
    accept(data, token(link))


def test_share_only_acceptance_and_graph_rebuild(
    links_data: InviteData,
    database_url: str,
    graph_environment: dict[str, str],
) -> None:
    data = links_data
    link = create(data)
    first = accept(data, token(link))
    assert accept(data, token(link)) == first
    config = GraphConfig.from_env()
    for _ in range(2):
        project_to_neo4j(database_url, config, airflow_run_id="social-share", clock=lambda: NOW)
    with GraphDatabase.driver(config.uri, auth=(config.user, config.password)) as driver:
        with driver.session(database=config.database) as session:
            rows = session.run(
                """
                MATCH (u:User {id:$user})-[i:INVITED_TO]->(e:CanonicalEvent {id:$event}),
                      (u)-[:ATTENDING]->(e)
                RETURN i.invited_by AS senders, i.status AS status, i.channel AS channel
            """,
                user=str(data.users[2]),
                event=str(data.event),
            ).data()
            assert rows == [
                {"senders": [str(data.users[0])], "status": "accepted", "channel": "share_link"}
            ]
            assert (
                session.run("MATCH ()-[f:FRIENDS_WITH]->() RETURN count(f) AS n").single()["n"] == 0
            )
    with data.engine.connect() as conn:
        original = conn.execute(text("SELECT * FROM invite_share_acceptance")).one()
    with data.client(0) as owner:
        assert owner.post(f"/api/invite-links/{link['id']}/revoke").status_code == 204
    with data.client(2) as recipient:
        assert recipient.post(f"/api/invite-links/{token(link)}/accept").status_code == 410
        assert recipient.get("/api/invites/received").json()[0]["status"] == "accepted"
    with data.engine.connect() as conn:
        assert conn.execute(text("SELECT * FROM invite_share_acceptance")).one() == original


def test_link_constraints_and_dedup_protection(links_data: InviteData) -> None:
    data = links_data
    create(data)
    with pytest.raises(IntegrityError), data.engine.begin() as conn:
        conn.execute(text("DELETE FROM canonical_event WHERE id=:id"), {"id": data.event})
    with pytest.raises(IntegrityError), data.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO invite_share_link
                (id,canonical_event_id,created_by,token_hash,created_at,expires_at)
            SELECT :id,canonical_event_id,created_by,token_hash,created_at,expires_at
            FROM invite_share_link LIMIT 1
        """),
            {"id": uuid4()},
        )


def test_expiry_setting_and_invalid_payload(
    links_data: InviteData, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVITE_LINK_TTL_SECONDS", "3600")
    link = create(links_data)
    assert link["expires_at"] == (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    with links_data.client(0) as client:
        assert (
            client.post(
                "/api/invite-links",
                json={
                    "canonical_event_id": str(links_data.event),
                    "created_by": str(links_data.users[1]),
                },
            ).status_code
            == 422
        )
        monkeypatch.setenv("INVITE_LINK_TTL_SECONDS", "invalid")
        assert (
            client.post(
                "/api/invite-links",
                json={
                    "canonical_event_id": str(links_data.event),
                },
            ).status_code
            == 503
        )


def test_share_migration_round_trip(database_url: str) -> None:
    config = migration_config(database_url)
    engine = create_engine(database_url)
    command.upgrade(config, "head")
    assert {"invite_share_link", "invite_share_acceptance"} <= table_names(engine)
    command.downgrade(config, "20260928_0018")
    assert not {"invite_share_link", "invite_share_acceptance"} & table_names(engine)
    command.upgrade(config, "head")
    assert {"invite_share_link", "invite_share_acceptance"} <= table_names(engine)
    engine.dispose()


def test_access_log_scope_is_redacted_without_breaking_routing() -> None:
    secret = "x" * 43
    scope: Scope = {
        "type": "http",
        "path": f"/api/invite-links/{secret}/accept",
        "raw_path": f"/api/invite-links/{secret}/accept".encode(),
        "query_string": b"",
    }

    async def endpoint(routed: Scope, receive: Receive, send: Send) -> None:
        assert routed["path"].endswith(secret + "/accept")

    async def receive() -> dict[str, object]:
        return {}

    async def send(message: dict[str, object]) -> None:
        pass

    asyncio.run(RedactInviteLinkAccessLogs(endpoint)(scope, receive, send))
    assert secret not in str(scope)
