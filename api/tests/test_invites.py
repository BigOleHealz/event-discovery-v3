from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import Response
from fastapi.testclient import TestClient
from ingestion.graph import GraphConfig, project_to_neo4j
from neo4j import GraphDatabase
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from test_auth import CONFIG, NOW

from app.clock import utc_now
from app.database import get_connection
from app.main import app
from app.oauth import auth_config
from app.sessions import SignedCookieSessions


@dataclass
class InviteData:
    engine: Engine
    event: UUID
    users: list[UUID]

    def client(self, number: int) -> TestClient:
        client = TestClient(app, base_url="https://api.example.test", raise_server_exceptions=False)
        store = SignedCookieSessions(CONFIG.secret, True, 900, 28800)
        response = Response()
        store.issue(response, self.users[number], NOW)
        token = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        client.cookies.set(store.cookie_name, token, domain="api.example.test", path="/")
        client.headers["Origin"] = CONFIG.web_url
        return client

    def send(self, sender: int, recipient: int = 2, message: str = "Join us!") -> dict[str, object]:
        with self.client(sender) as client:
            response = client.post(
                "/api/invites",
                json={
                    "canonical_event_id": str(self.event),
                    "emails": [f"person{recipient}@example.com"],
                    "message": message,
                },
            )
            assert response.status_code == 200, response.text
            return dict(response.json()[0])


@pytest.fixture
def invite_data(database_url: str) -> Iterator[InviteData]:
    migration = Config("alembic.ini")
    migration.attributes["database_url"] = database_url
    command.upgrade(migration, "head")
    engine = create_engine(database_url)
    data = InviteData(engine, uuid4(), [uuid4() for _ in range(4)])
    with engine.begin() as conn:
        for number, user_id in enumerate(data.users):
            conn.execute(
                text("""
                INSERT INTO app_user (id, google_sub, email, display_name)
                VALUES (:id, :sub, :email, :name)
            """),
                {
                    "id": user_id,
                    "sub": f"invite-person-{number}",
                    "email": f"person{number}@example.com",
                    "name": f"Person {number}",
                },
            )
        conn.execute(
            text("""
            INSERT INTO canonical_event (id, title, starts_at, timezone, location)
            VALUES (:id, 'Evening jazz', :starts, 'UTC',
                    ST_SetSRID(ST_MakePoint(-75.16, 39.95),4326)::geography)
        """),
            {"id": data.event, "starts": NOW + timedelta(days=1)},
        )

    def database() -> Iterator[Connection]:
        with engine.connect() as connection:
            yield connection

    app.dependency_overrides[get_connection] = database
    app.dependency_overrides[auth_config] = lambda: CONFIG
    app.dependency_overrides[utc_now] = lambda: NOW
    try:
        yield data
    finally:
        app.dependency_overrides.clear()
        with engine.begin() as conn:
            conn.execute(
                text("""
                DELETE FROM ingest.run WHERE airflow_dag_id = 'project_to_neo4j'
                AND airflow_run_id LIKE 'social-%'
            """)
            )
            conn.execute(
                text("DELETE FROM attendance WHERE canonical_event_id = :event"),
                {"event": data.event},
            )
            conn.execute(
                text("DELETE FROM invite WHERE canonical_event_id = :event"), {"event": data.event}
            )
            conn.execute(
                text("DELETE FROM canonical_event WHERE id = :event"), {"event": data.event}
            )
            conn.execute(text("DELETE FROM app_user WHERE id = ANY(:ids)"), {"ids": data.users})
        engine.dispose()


def test_repeat_and_concurrent_senders_append_one_invite(invite_data: InviteData) -> None:
    data = invite_data
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(data.send, [0, 1]))
    assert results[0]["id"] == results[1]["id"]
    repeated = data.send(0, message="Do not overwrite original provenance")
    assert set(repeated["invited_by"]) == {str(data.users[0]), str(data.users[1])}
    assert len(repeated["invited_by"]) == 2
    assert repeated["message"] == "Join us!"
    with data.engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM invite WHERE canonical_event_id=:id"), {"id": data.event}
            )
            == 1
        )
    with data.client(0) as sender:
        assert len(sender.get("/api/invites/sent").json()) == 1


def test_partial_unique_index_rejects_duplicate(invite_data: InviteData) -> None:
    data = invite_data
    data.send(0)
    with pytest.raises(IntegrityError), data.engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO invite (id, canonical_event_id, to_user_id, invited_by)
            VALUES (:id, :event, :recipient, ARRAY[CAST(:sender AS uuid)])
        """),
            {
                "id": uuid4(),
                "event": data.event,
                "recipient": data.users[2],
                "sender": data.users[1],
            },
        )


def test_accept_is_idempotent_and_preserves_provenance(invite_data: InviteData) -> None:
    data = invite_data
    invitation = data.send(0)
    with data.client(2) as recipient:
        first = recipient.post(
            f"/api/invites/{invitation['id']}/respond", json={"response": "accept"}
        )
        assert first.status_code == 200
        assert first.json()["status"] == "accepted"
        again = recipient.post(
            f"/api/invites/{invitation['id']}/respond", json={"response": "accept"}
        )
        assert again.json() == first.json()
    updated = data.send(1)
    assert updated["status"] == "accepted"
    assert updated["responded_at"] == first.json()["responded_at"]
    with data.engine.connect() as conn:
        row = conn.execute(
            text("SELECT state, source FROM attendance WHERE user_id=:id"), {"id": data.users[2]}
        ).one()
        assert tuple(row) == ("attending", "invite_accept")
        assert (
            conn.scalar(text("SELECT count(*) FROM invite WHERE id=:id"), {"id": invitation["id"]})
            == 1
        )


def test_decline_and_change_of_mind_only_remove_invite_attendance(invite_data: InviteData) -> None:
    data = invite_data
    invitation = data.send(0)
    path = f"/api/invites/{invitation['id']}/respond"
    with data.client(2) as recipient:
        for response in ("decline", "accept", "decline", "accept"):
            result = recipient.post(path, json={"response": response})
            assert result.status_code == 200
            with data.engine.connect() as conn:
                assert conn.scalar(
                    text("SELECT count(*) FROM attendance WHERE user_id=:id"), {"id": data.users[2]}
                ) == (1 if response == "accept" else 0)
        with data.engine.begin() as conn:
            conn.execute(
                text("UPDATE attendance SET source='self_rsvp' WHERE user_id=:id"),
                {"id": data.users[2]},
            )
        assert recipient.post(path, json={"response": "decline"}).status_code == 200
    with data.engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT source FROM attendance WHERE user_id=:id"), {"id": data.users[2]}
            )
            == "self_rsvp"
        )


def test_invite_reads_and_responses_are_scoped_to_session(invite_data: InviteData) -> None:
    data = invite_data
    invitation = data.send(0)
    with data.client(3) as stranger, data.client(0) as sender, data.client(2) as recipient:
        for client in (stranger, sender):
            response = client.get("/api/invites/received", params={"user_id": str(data.users[2])})
            assert response.json() == []
            assert response.headers["cache-control"] == "no-store"
            assert (
                client.post(
                    f"/api/invites/{invitation['id']}/respond", json={"response": "accept"}
                ).status_code
                == 404
            )
        assert (
            stranger.get("/api/invites/sent", params={"user_id": str(data.users[0])}).json() == []
        )
        assert recipient.get("/api/invites/received").json()[0]["id"] == invitation["id"]
        assert "example.com" not in recipient.get("/api/invites/received").text
        assert (
            sender.post(
                "/api/invites",
                headers={"Origin": "https://evil.test"},
                json={
                    "canonical_event_id": str(data.event),
                    "emails": ["person2@example.com"],
                },
            ).status_code
            == 403
        )
        assert (
            recipient.post(
                f"/api/invites/{invitation['id']}/respond",
                headers={"Origin": "https://evil.test"},
                json={"response": "accept"},
            ).status_code
            == 403
        )
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/invites/received").status_code == 401
        assert anonymous.get("/api/invites/sent").status_code == 401


def test_batch_validation_pagination_and_past_events(invite_data: InviteData) -> None:
    data = invite_data
    with data.client(0) as sender:
        for emails in (
            [],
            ["not-email"],
            ["person0@example.com"],
            ["person2@example.com", "unknown@example.com"],
        ):
            assert (
                sender.post(
                    "/api/invites",
                    json={
                        "canonical_event_id": str(data.event),
                        "emails": emails,
                    },
                ).status_code
                == 422
            )
        assert sender.get("/api/invites/sent").json() == []
        result = sender.post(
            "/api/invites",
            json={
                "canonical_event_id": str(data.event),
                "emails": ["PERSON2@example.com", "person2@example.com", "person3@example.com"],
            },
        )
        assert result.status_code == 200
        assert len(result.json()) == 2
        assert len(sender.get("/api/invites/sent?limit=1").json()) == 1
        assert len(sender.get("/api/invites/sent?limit=1&offset=1").json()) == 1
        assert sender.get("/api/invites/sent?limit=101").status_code == 422
        assert (
            sender.post(
                "/api/invites",
                json={
                    "canonical_event_id": str(uuid4()),
                    "emails": ["person2@example.com"],
                },
            ).status_code
            == 404
        )
        with data.engine.begin() as conn:
            conn.execute(
                text("UPDATE canonical_event SET starts_at=:now WHERE id=:id"),
                {"now": NOW, "id": data.event},
            )
        assert (
            sender.post(
                "/api/invites",
                json={
                    "canonical_event_id": str(data.event),
                    "emails": ["person2@example.com"],
                },
            ).status_code
            == 404
        )
    recipient_invite = next(row for row in result.json() if row["to_user_id"] == str(data.users[2]))
    with data.client(2) as recipient:
        assert (
            recipient.post(
                f"/api/invites/{recipient_invite['id']}/respond", json={"response": "accept"}
            ).status_code
            == 409
        )


def test_accept_rolls_back_invite_if_attendance_write_fails(invite_data: InviteData) -> None:
    data = invite_data
    invitation = data.send(0)
    with data.engine.begin() as conn:
        conn.execute(text("ALTER TABLE attendance ADD CONSTRAINT fixture_reject CHECK (false)"))
    try:
        with data.client(2) as recipient:
            assert (
                recipient.post(
                    f"/api/invites/{invitation['id']}/respond", json={"response": "accept"}
                ).status_code
                == 500
            )
            assert recipient.get("/api/invites/received").json()[0]["status"] == "pending"
    finally:
        with data.engine.begin() as conn:
            conn.execute(text("ALTER TABLE attendance DROP CONSTRAINT fixture_reject"))


def test_graph_rebuild_keeps_one_invitee_edge_and_attendance(
    invite_data: InviteData,
    database_url: str,
    graph_environment: GraphConfig,
) -> None:
    data = invite_data
    invitation = data.send(0)
    data.send(1)
    pending = project_to_neo4j(
        database_url, graph_environment, airflow_run_id="social-pending", clock=lambda: NOW
    )
    assert pending["INVITED_TO"] == 1
    assert pending["ATTENDING"] == 0
    with data.client(2) as recipient:
        assert (
            recipient.post(
                f"/api/invites/{invitation['id']}/respond", json={"response": "accept"}
            ).status_code
            == 200
        )
    with GraphDatabase.driver(
        graph_environment.uri, auth=(graph_environment.user, graph_environment.password)
    ) as driver:
        for run in ("social-first", "social-repeat", "social-repair"):
            if run == "social-repair":
                with driver.session() as session:
                    session.run("MATCH (n) DETACH DELETE n").consume()
            counts = project_to_neo4j(
                database_url, graph_environment, airflow_run_id=run, clock=lambda: NOW
            )
            assert counts["INVITED_TO"] == 1
            assert counts["ATTENDING"] == 1
            with driver.session() as session:
                rows = session.run(
                    """
                    MATCH (u:User)-[i:INVITED_TO]->(e:CanonicalEvent)
                    MATCH (u)-[a:ATTENDING]->(e)
                    RETURN u.id AS user_id, e.id AS event_id, i.invited_by AS senders,
                           i.status AS status, a.attendance_source AS source,
                           i.sent_at = datetime($now) AS sent_at_matches,
                           i.responded_at = datetime($now) AS responded_at_matches,
                           keys(u) AS user_properties
                """,
                    now=NOW.isoformat(),
                ).data()
                assert len(rows) == 1
                assert rows[0]["user_id"] == str(data.users[2])
                assert rows[0]["event_id"] == str(data.event)
                assert set(rows[0]["senders"]) == {str(data.users[0]), str(data.users[1])}
                assert rows[0]["status"] == "accepted"
                assert rows[0]["sent_at_matches"] is True
                assert rows[0]["responded_at_matches"] is True
                assert rows[0]["source"] == "invite_accept"
                assert set(rows[0]["user_properties"]) <= {
                    "id",
                    "display_name",
                    "avatar_url",
                    "is_shadow",
                }
        with data.client(2) as recipient:
            recipient.post(f"/api/invites/{invitation['id']}/respond", json={"response": "decline"})
        counts = project_to_neo4j(
            database_url, graph_environment, airflow_run_id="social-decline", clock=lambda: NOW
        )
        assert counts["INVITED_TO"] == 1
        assert counts["ATTENDING"] == 0
