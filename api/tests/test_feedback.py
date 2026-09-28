from collections.abc import Iterator
from datetime import timedelta

import pytest
from ingestion.feedback import request_event_feedback
from ingestion.graph import GraphConfig, project_to_neo4j
from neo4j import GraphDatabase
from sqlalchemy import text
from test_auth import NOW
from test_invites import InviteData, invite_data  # noqa: F401

from app.clock import utc_now
from app.main import app


@pytest.fixture
def feedback_data(invite_data: InviteData, database_url: str) -> Iterator[InviteData]:  # noqa: F811
    data = invite_data
    invite = data.send(0)
    with data.client(2) as recipient:
        assert (
            recipient.post(
                f"/api/invites/{invite['id']}/respond", json={"response": "accept"}
            ).status_code
            == 200
        )
    with data.engine.begin() as conn:
        conn.execute(
            text("""
            UPDATE canonical_event SET starts_at=:starts, ends_at=:ends WHERE id=:id
        """),
            {"id": data.event, "starts": NOW - timedelta(days=2), "ends": NOW - timedelta(days=1)},
        )
    assert request_event_feedback(
        database_url, airflow_run_id="feedback-api", clock=lambda: NOW
    ) == {
        "attended": 1,
        "requested": 1,
    }
    try:
        yield data
    finally:
        with data.engine.begin() as conn:
            conn.execute(text("DELETE FROM ingest.run WHERE airflow_run_id LIKE 'feedback-%'"))


def test_feedback_is_persisted_editable_and_retry_safe(feedback_data: InviteData) -> None:
    data = feedback_data
    with data.client(2) as recipient:
        listing = recipient.get("/api/attendance/feedback")
        assert listing.status_code == 200
        assert listing.headers["cache-control"] == "no-store"
        entry = listing.json()[0]
        path = f"/api/attendance/{entry['attendance_id']}/feedback"
        result = recipient.post(path, json={"rating": 5, "feedback_text": "  Great show!  "})
        assert result.status_code == 200
        assert result.json()["feedback_text"] == "Great show!"
        assert result.json()["rating"] == 5
        assert result.headers["cache-control"] == "no-store"
        app.dependency_overrides[utc_now] = lambda: NOW + timedelta(minutes=1)
        assert (
            recipient.post(path, json={"rating": 5, "feedback_text": "Great show!"}).json()
            == result.json()
        )
        assert recipient.get("/api/attendance/feedback").json() == []
        reviewed = recipient.get("/api/attendance/feedback?completed=true").json()
        assert reviewed == [result.json()]
        changed = recipient.post(path, json={"rating": 4, "feedback_text": "Good, loud room."})
        assert changed.json()["rating"] == 4
        assert changed.json()["feedback_at"] != result.json()["feedback_at"]
    with data.engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT state FROM attendance WHERE user_id=:id"), {"id": data.users[2]}
            )
            == "attended"
        )
        assert (
            conn.scalar(
                text("SELECT status FROM invite WHERE to_user_id=:id"), {"id": data.users[2]}
            )
            == "accepted"
        )


def test_feedback_requires_owner_valid_rating_and_elapsed_event(feedback_data: InviteData) -> None:
    data = feedback_data
    with data.client(2) as recipient, data.client(0) as sender:
        entry = recipient.get("/api/attendance/feedback").json()[0]
        path = f"/api/attendance/{entry['attendance_id']}/feedback"
        assert (
            sender.get("/api/attendance/feedback", params={"user_id": str(data.users[2])}).json()
            == []
        )
        assert sender.post(path, json={"rating": 5}).status_code == 404
        for rating in (0, 6, True, 3.5, "5", None):
            assert recipient.post(path, json={"rating": rating}).status_code == 422
        assert (
            recipient.post(path, json={"rating": 5, "feedback_text": "x" * 2001}).status_code == 422
        )
        assert (
            recipient.post(
                path, headers={"Origin": "https://evil.test"}, json={"rating": 5}
            ).status_code
            == 403
        )
        with data.engine.begin() as conn:
            conn.execute(
                text("UPDATE canonical_event SET ends_at=:future WHERE id=:id"),
                {"id": data.event, "future": NOW + timedelta(days=1)},
            )
        assert recipient.get("/api/attendance/feedback").json() == []
        assert recipient.post(path, json={"rating": 5}).status_code == 409


def test_attended_projection_contains_feedback_and_keeps_invite_history(
    feedback_data: InviteData,
    database_url: str,
    graph_environment: GraphConfig,
) -> None:
    data = feedback_data
    with data.client(2) as recipient:
        entry = recipient.get("/api/attendance/feedback").json()[0]
        assert (
            recipient.post(
                f"/api/attendance/{entry['attendance_id']}/feedback",
                json={"rating": 4, "feedback_text": "Great music"},
            ).status_code
            == 200
        )
    for run in ("feedback-graph", "feedback-graph-repeat"):
        counts = project_to_neo4j(
            database_url, graph_environment, airflow_run_id=run, clock=lambda: NOW
        )
        assert counts["ATTENDED"] == 1
        assert counts["ATTENDING"] == 0
        assert counts["INVITED_TO"] == 1
        with GraphDatabase.driver(
            graph_environment.uri, auth=(graph_environment.user, graph_environment.password)
        ) as driver:
            with driver.session() as session:
                row = session.run(
                    """
                    MATCH (u:User {id:$user})-[a:ATTENDED]->(e:CanonicalEvent {id:$event})
                    MATCH (u)-[i:INVITED_TO]->(e)
                    RETURN a.rating AS rating, a.feedback_text AS feedback,
                           a.feedback_at=datetime($now) AS feedback_at_matches,
                           i.status AS status, i.invited_by AS invited_by
                """,
                    user=str(data.users[2]),
                    event=str(data.event),
                    now=NOW.isoformat(),
                ).single()
                assert row is not None
                assert row.data() == {
                    "rating": 4,
                    "feedback": "Great music",
                    "feedback_at_matches": True,
                    "status": "accepted",
                    "invited_by": [str(data.users[0])],
                }
