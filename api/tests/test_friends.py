from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from ingestion.graph import GraphConfig, project_to_neo4j
from neo4j import GraphDatabase
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from test_auth import NOW
from test_invites import InviteData, invite_data  # noqa: F401

from app.graph import graph_driver
from app.main import app

BOUNDS = {"north": 40, "south": 39, "east": -75, "west": -76, "zoom": 14}


@pytest.fixture
def data(invite_data: InviteData, graph_environment: GraphConfig) -> Iterator[InviteData]:  # noqa: F811
    yield invite_data
    with invite_data.engine.begin() as connection:
        connection.execute(text("DELETE FROM friendship"))


def befriend(data: InviteData, sender: int = 0, recipient: int = 2) -> None:
    with data.client(sender) as a, data.client(recipient) as b:
        assert (
            a.post("/api/friends", json={"email": f"person{recipient}@example.com"}).status_code
            == 204
        )
        assert b.post(f"/api/friends/{data.users[sender]}/accept").status_code == 204


def accept_invite(data: InviteData, sender: int = 0, recipient: int = 2) -> None:
    invite = data.send(sender, recipient)
    with data.client(recipient) as client:
        assert (
            client.post(
                f"/api/invites/{invite['id']}/respond", json={"response": "accept"}
            ).status_code
            == 200
        )


def test_mutual_consent_private_lists_and_duplicate_constraint(data: InviteData) -> None:
    with data.client(0) as a, data.client(2) as b, data.client(3) as stranger:
        for _ in range(2):
            assert a.post("/api/friends", json={"email": "person2@example.com"}).status_code == 204
        assert b.post("/api/friends", json={"email": "person0@example.com"}).status_code == 204
        assert a.get("/api/friends").json()[0]["status"] == "outgoing"
        assert b.get("/api/friends").json()[0]["status"] == "incoming"
        assert stranger.get("/api/friends").json() == []
        assert a.post(f"/api/friends/{data.users[2]}/accept").status_code == 404
        assert stranger.post(f"/api/friends/{data.users[0]}/accept").status_code == 404
        for _ in range(2):
            assert b.post(f"/api/friends/{data.users[0]}/accept").status_code == 204
        response = a.get("/api/friends")
        assert response.headers["cache-control"] == "no-store"
        assert response.json()[0]["status"] == "accepted"
        assert "email" not in response.json()[0]
    low, high = sorted((data.users[0], data.users[2]))
    with data.engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text("""
            INSERT INTO friendship (user_low,user_high,requested_by,created_at)
            VALUES (:low,:high,:low,:now)
        """),
            {"low": low, "high": high, "now": NOW},
        )


def test_friends_layer_immediate_private_and_rebuildable(
    data: InviteData,
    database_url: str,
    graph_environment: GraphConfig,
) -> None:
    accept_invite(data)
    with data.client(0) as a:
        assert a.get("/api/events/friends", params=BOUNDS).json()["events"] == []
    befriend(data)
    with data.client(0) as a, data.client(3) as stranger:
        response = a.get("/api/events/friends", params=BOUNDS)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        event = response.json()["events"][0]
        assert event["event_id"] == str(data.event)
        assert event["friends"] == [
            {"id": str(data.users[2]), "display_name": "Person 2", "avatar_url": None}
        ]
        assert stranger.get("/api/events/friends", params=BOUNDS).json()["events"] == []
        assert stranger.get(f"/api/events/{data.event}/friends").json() == []
        assert a.get(f"/api/events/{data.event}/friends").json() == event["friends"]
        # A fresh projection, repeated twice, retains one undirected pair and attendance.
        for _ in range(2):
            counts = project_to_neo4j(
                database_url, graph_environment, airflow_run_id="social-friends", clock=lambda: NOW
            )
            assert counts["FRIENDS_WITH"] == 1
            assert counts["ATTENDING"] == 1
        with GraphDatabase.driver(
            graph_environment.uri, auth=("neo4j", graph_environment.password)
        ) as driver:
            with driver.session() as session:
                assert (
                    session.run("MATCH ()-[r:FRIENDS_WITH]->() RETURN count(r) AS n").single()["n"]
                    == 1
                )
        assert a.post(f"/api/friends/{data.users[2]}/remove").status_code == 204
        # The old graph edge cannot leak attendance after removing a friend.
        assert a.get("/api/events/friends", params=BOUNDS).json()["events"] == []


def test_pending_declined_cancelled_and_self_requests(data: InviteData) -> None:
    accept_invite(data)
    with data.client(0) as a, data.client(2) as b:
        assert a.post("/api/friends", json={"email": "person0@example.com"}).status_code == 422
        assert a.post("/api/friends", json={"email": "absent@example.com"}).status_code == 422
        assert (
            a.post(
                "/api/friends", json={"email": "person2@example.com", "user_id": str(data.users[1])}
            ).status_code
            == 422
        )
        a.post("/api/friends", json={"email": "person2@example.com"})
        assert a.get("/api/events/friends", params=BOUNDS).json()["events"] == []
        assert b.post(f"/api/friends/{data.users[0]}/remove").status_code == 204
        assert b.post(f"/api/friends/{data.users[0]}/accept").status_code == 404
        a.post("/api/friends", json={"email": "person2@example.com"})
        a.post(f"/api/friends/{data.users[2]}/remove")
        assert a.get("/api/friends").json() == []


def test_layer_filters_aggregates_and_current_attendance(data: InviteData) -> None:
    befriend(data)
    befriend(data, recipient=1)
    accept_invite(data)
    accept_invite(data, recipient=1)
    with data.client(0) as a:
        result = a.get("/api/events/friends", params={**BOUNDS, "zoom": 12})
        assert result.status_code == 200, result.text
        public = a.get("/api/events", params={**BOUNDS, "zoom": 12}).json()
        assert result.json() == {
            "events": [],
            "cells": [{"cell_id": public["features"][0]["id"], "event_count": 1}],
        }
        assert (
            a.get("/api/events/friends", params={**BOUNDS, "east": -74, "west": -75}).json()[
                "events"
            ]
            == []
        )
        assert (
            a.get("/api/events/friends", params={**BOUNDS, "time_of_day_start": "23:00"}).json()[
                "events"
            ]
            == []
        )
        with data.engine.begin() as conn:
            conn.execute(text("UPDATE attendance SET state='attended'"))
        assert a.get("/api/events/friends", params=BOUNDS).json()["events"] == []
        with data.engine.begin() as conn:
            conn.execute(text("UPDATE attendance SET state='attending'"))
            conn.execute(
                text("UPDATE canonical_event SET starts_at=:past WHERE id=:id"),
                {"past": NOW - timedelta(days=1), "id": data.event},
            )
        assert a.get("/api/events/friends", params=BOUNDS).json()["events"] == []
        assert a.get(
            "/api/events/friends",
            params={**BOUNDS, "starts_after": (NOW - timedelta(days=2)).isoformat()},
        ).json()["events"]
        with data.engine.begin() as conn:
            conn.execute(
                text("UPDATE canonical_event SET archived_at=:now WHERE id=:id"),
                {"now": NOW, "id": data.event},
            )
        assert a.get(f"/api/events/{data.event}/friends").json() == []


def test_auth_origin_and_viewport_required(data: InviteData) -> None:
    with TestClient(app, base_url="https://api.example.test") as anonymous:
        for path in ("/api/friends", "/api/events/friends", f"/api/events/{data.event}/friends"):
            response = anonymous.get(path, params=BOUNDS)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
    with data.client(0) as a:
        assert a.get("/api/events/friends").status_code == 422
        assert a.get("/api/events/friends", params={**BOUNDS, "north": 38}).status_code == 422
        for path in (
            "/api/friends",
            f"/api/friends/{uuid4()}/accept",
            f"/api/friends/{uuid4()}/remove",
        ):
            assert (
                a.post(
                    path,
                    headers={"Origin": "https://evil.test"},
                    json={"email": "person2@example.com"},
                ).status_code
                == 403
            )


def test_concurrent_reverse_requests_stay_one_pending_pair(data: InviteData) -> None:
    def request(sender: int, recipient: int) -> int:
        with data.client(sender) as client:
            return client.post(
                "/api/friends", json={"email": f"person{recipient}@example.com"}
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(request, 0, 2), pool.submit(request, 2, 0)]
        assert [result.result() for result in results] == [204, 204]
    with data.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM friendship")) == 1
        assert conn.scalar(text("SELECT accepted_at FROM friendship")) is None


def test_graph_failure_does_not_break_public_events(
    data: InviteData, monkeypatch: pytest.MonkeyPatch
) -> None:
    befriend(data)
    graph_driver().close()
    graph_driver.cache_clear()
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    try:
        with data.client(0) as a:
            assert a.get("/api/events/friends", params=BOUNDS).status_code == 503
            assert a.get("/api/events", params=BOUNDS).status_code == 200
    finally:
        graph_driver().close()
        graph_driver.cache_clear()
