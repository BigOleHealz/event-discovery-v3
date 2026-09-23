from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import UUID, uuid4

import anyio
import pytest
import sqlalchemy as sa
from alembic import command
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.exc import IntegrityError
from test_migrations import migration_config

from app import review_admin
from app.clock import utc_now
from app.database import get_connection
from app.main import app

NOW = datetime.fromisoformat("2026-09-22T12:00:00+00:00")
REVIEWER = UUID("4f000000-0000-0000-0000-000000000001")
TOKEN = "fixture-admin-token"


@pytest.mark.parametrize("missing", ["ADMIN_REVIEW_TOKEN", "ADMIN_REVIEW_USER_ID"])
def test_provisioning_disabled_without_both_settings(
    monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    monkeypatch.setenv("ADMIN_REVIEW_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_REVIEW_USER_ID", str(REVIEWER))
    monkeypatch.delenv(missing)

    def unexpected_database_access() -> sa.Engine:
        raise AssertionError("Disabled review must not access the database")

    monkeypatch.setattr(review_admin, "get_engine", unexpected_database_access)
    review_admin.main()


def test_provisioning_creates_once_and_preserves_existing_user(
    review_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review_admin, "get_engine", lambda: review_engine)
    monkeypatch.setenv("SEED_DATABASE", "false")
    with review_engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM app_user WHERE id = :id"), {"id": REVIEWER})
    review_admin.main()
    with review_engine.begin() as connection:
        assert connection.execute(sa.text("""
            SELECT id, is_shadow FROM app_user
        """)).all() == [(REVIEWER, True)]
        connection.execute(sa.text("""
            UPDATE app_user SET display_name = 'Keep my name', google_sub = 'claimed-reviewer',
                is_shadow = false WHERE id = :id
        """), {"id": REVIEWER})
    review_admin.main()
    with review_engine.connect() as connection:
        assert connection.execute(sa.text("""
            SELECT id, display_name, google_sub, is_shadow FROM app_user
        """)).all() == [(REVIEWER, "Keep my name", "claimed-reviewer", False)]


def seed_pair(engine: sa.Engine) -> tuple[UUID, UUID]:
    a, b = sorted((uuid4(), uuid4()))
    with engine.begin() as connection:
        for listing, source, title in ((a, "meetup", "Meetup original"),
                                       (b, "eventbrite", "Eventbrite original")):
            connection.execute(sa.text("""
                INSERT INTO canonical_event (
                    id, title, description, starts_at, timezone, location, primary_category
                ) VALUES (:id, :title, :description, '2026-10-06T23:30:00Z',
                    'America/New_York', ST_SetSRID(ST_MakePoint(-75.185, 39.9522), 4326),
                    'review-test')
            """), {"id": listing, "title": title, "description": title + " description"})
            connection.execute(sa.text("""
                INSERT INTO source_listing (
                    id, canonical_event_id, source, source_event_id, url, raw_payload,
                    ingestion_run_id, dedup_state
                ) VALUES (:id, :id, :source, :source_id, :url, '{}', :run, 'distinct')
            """), {"id": listing, "source": source, "source_id": str(listing),
                   "url": f"https://{source}.test/{listing}", "run": uuid4()})
        connection.execute(sa.text("""
            UPDATE source_listing SET dedup_state = 'review', dedup_match_id = :a,
                dedup_similarity = 0.8, dedup_checked_at = :now WHERE id = :b
        """), {"a": a, "b": b, "now": NOW})
    return a, b


@pytest.fixture
def review_engine(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.Engine]:
    config = migration_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO app_user (id, is_shadow) VALUES (:id, true)
        """), {"id": REVIEWER})
    monkeypatch.setenv("ADMIN_REVIEW_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_REVIEW_USER_ID", str(REVIEWER))
    monkeypatch.setenv("DEDUP_SOURCE_PRIORITY", "eventbrite,meetup")

    def connection() -> Iterator[sa.Connection]:
        with engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_connection] = connection
    app.dependency_overrides[utc_now] = lambda: NOW
    try:
        yield engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        command.downgrade(config, "base")


def request(path: str = "", body: dict | None = None, token: str | None = TOKEN) -> Response:
    async def send() -> Response:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            if body is None:
                return await client.get(f"/api/admin/dedup{path}", headers=headers)
            return await client.post(f"/api/admin/dedup{path}", headers=headers, json=body)
    return anyio.run(send)


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_admin_required_for_reads_and_writes(review_engine: sa.Engine, token: str | None) -> None:
    seed_pair(review_engine)
    assert request(token=token).status_code == 401
    assert request(f"/{uuid4()}/decision", {
        "status": "merged", "event_a_id": str(uuid4()), "event_b_id": str(uuid4())
    }, token).status_code == 401


@pytest.mark.parametrize("setting,value", [
    ("ADMIN_REVIEW_TOKEN", ""), ("ADMIN_REVIEW_USER_ID", ""),
    ("ADMIN_REVIEW_USER_ID", "invalid"), ("ADMIN_REVIEW_USER_ID", str(uuid4())),
])
def test_admin_configuration_fails_closed(
    review_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch, setting: str, value: str,
) -> None:
    monkeypatch.setenv(setting, value)
    assert request().status_code == 503


def test_queue_evidence_order_reverse_pair_and_no_implicit_merge(review_engine: sa.Engine) -> None:
    a, b = seed_pair(review_engine)
    with review_engine.begin() as connection:
        connection.execute(sa.text("""
            UPDATE source_listing SET dedup_state = 'review', dedup_match_id = :b,
                dedup_similarity = 0.8, dedup_checked_at = :now WHERE id = :a
        """), {"a": a, "b": b, "now": NOW})
    response = request()
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    queue = response.json()
    assert queue["pending"] == 1
    pair = queue["pair"]
    assert pair["snapshot_a"]["title"] == "Meetup original"
    assert pair["snapshot_b"]["description"] == "Eventbrite original description"
    assert pair["snapshot_a"]["url"].startswith("https://meetup.test/")
    assert pair["event_a_id"] != pair["event_b_id"]
    with review_engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM canonical_event")) == 2
        assert connection.scalar(sa.text("SELECT created_at FROM dedup_review")) == NOW


@pytest.mark.parametrize("status", ["merged", "distinct", "skipped"])
def test_decision_audit_idempotency_and_canonical_behavior(
    review_engine: sa.Engine, status: str,
) -> None:
    a, b = seed_pair(review_engine)
    pair = request().json()["pair"]
    body = {"status": status, "event_a_id": str(a), "event_b_id": str(b)}
    path = f"/{pair['id']}/decision"
    first = request(path, body)
    assert first.status_code == 200, first.text
    assert first.json()["decided_by"] == str(REVIEWER)
    assert datetime.fromisoformat(first.json()["decided_at"]) == NOW
    assert request(path, body).json() == first.json()
    assert request(path, {**body, "status": "skipped" if status != "skipped" else "merged"}
                   ).status_code == 409
    assert request().json() == {"pending": 0, "pair": None}
    with review_engine.connect() as connection:
        events = connection.execute(sa.text("SELECT id, title FROM canonical_event")).all()
        assert len(events) == (1 if status == "merged" else 2)
        if status == "merged":
            assert events == [(b, "Eventbrite original")]
            assert connection.scalar(sa.text("""
                SELECT count(DISTINCT canonical_event_id) FROM source_listing
            """)) == 1
        snapshots = connection.execute(sa.text("""
            SELECT snapshot_a, snapshot_b FROM dedup_review
        """)).one()
        assert snapshots[0]["title"] == "Meetup original"
        assert snapshots[1]["title"] == "Eventbrite original"


def test_stale_invalid_and_missing_decisions(review_engine: sa.Engine) -> None:
    a, b = seed_pair(review_engine)
    pair = request().json()["pair"]
    body = {"status": "merged", "event_a_id": str(a), "event_b_id": str(b)}
    assert request(f"/{uuid4()}/decision", body).status_code == 404
    path = f"/{pair['id']}/decision"
    assert request(path, {**body, "status": "typo"}).status_code == 422
    assert request(path, {**body, "event_a_id": str(uuid4())}).status_code == 409
    assert request().json()["pending"] == 1


def test_merge_rolls_back_when_loser_has_dependent_data(review_engine: sa.Engine) -> None:
    a, b = seed_pair(review_engine)
    pair = request().json()["pair"]
    with review_engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO notification_log (id, user_id, canonical_event_id, channel, trigger)
            VALUES (:id, :user, :event, 'push', 'nearby')
        """), {"id": uuid4(), "user": REVIEWER, "event": a})
    response = request(f"/{pair['id']}/decision", {
        "status": "merged", "event_a_id": str(a), "event_b_id": str(b),
    })
    assert response.status_code == 409
    assert request().json()["pending"] == 1
    with review_engine.connect() as connection:
        assert connection.scalar(sa.text("""
            SELECT count(DISTINCT canonical_event_id) FROM source_listing
        """)) == 2


def test_concurrent_conflicting_decisions_only_one_wins(review_engine: sa.Engine) -> None:
    a, b = seed_pair(review_engine)
    pair = request().json()["pair"]
    def decide(status: str) -> int:
        return request(f"/{pair['id']}/decision", {
            "status": status, "event_a_id": str(a), "event_b_id": str(b),
        }).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(decide, ["merged", "distinct"])) == [200, 409]


def test_distinct_label_blocks_transitive_manual_merge(review_engine: sa.Engine) -> None:
    a, b = seed_pair(review_engine)
    first = request().json()["pair"]
    assert request(f"/{first['id']}/decision", {
        "status": "distinct", "event_a_id": str(a), "event_b_id": str(b),
    }).status_code == 200
    c, d = seed_pair(review_engine)
    with review_engine.begin() as connection:
        for listing, canonical in ((c, a), (d, b)):
            connection.execute(sa.text("""
                UPDATE source_listing SET canonical_event_id = :canonical WHERE id = :listing
            """), {"canonical": canonical, "listing": listing})
            connection.execute(
                sa.text("DELETE FROM canonical_event WHERE id = :id"), {"id": listing}
            )
    pair = request().json()["pair"]
    response = request(f"/{pair['id']}/decision", {
        "status": "merged", "event_a_id": pair["event_a_id"], "event_b_id": pair["event_b_id"],
    })
    assert response.status_code == 409
    assert "Distinct" in response.json()["detail"]
    assert request().json()["pending"] == 1


def test_merge_moves_entire_group_and_uses_configured_priority(
    review_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = seed_pair(review_engine)
    extra = uuid4()
    with review_engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO source_listing (
                id, canonical_event_id, source, source_event_id, url, raw_payload, ingestion_run_id
            ) VALUES (:id, :event, 'fixture', :source_id, 'https://fixture.test', '{}', :run)
        """), {"id": extra, "event": b, "source_id": str(extra), "run": uuid4()})
    monkeypatch.setenv("DEDUP_SOURCE_PRIORITY", "meetup,eventbrite")
    pair = request().json()["pair"]
    assert request(f"/{pair['id']}/decision", {
        "status": "merged", "event_a_id": str(a), "event_b_id": str(b),
    }).status_code == 200
    with review_engine.connect() as connection:
        assert connection.execute(sa.text("SELECT id FROM canonical_event")).all() == [(a,)]
        assert connection.scalar(sa.text("""
            SELECT count(*) FROM source_listing WHERE canonical_event_id = :id
        """), {"id": a}) == 3


def test_review_migration_backfills_and_reverses(database_url: str) -> None:
    config = migration_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260916_0009")
    engine = sa.create_engine(database_url)
    a, b = seed_pair(engine)
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM dedup_review")) == 1
    for assignment in ["listing_b_id = listing_a_id", "status = 'typo'", "status = 'merged'"]:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(sa.text(f"UPDATE dedup_review SET {assignment}"))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO dedup_review (
                listing_a_id, listing_b_id, similarity_score, time_delta_minutes,
                distance_meters, snapshot_a, snapshot_b
            ) SELECT listing_a_id, listing_b_id, similarity_score, time_delta_minutes,
                     distance_meters, snapshot_a, snapshot_b FROM dedup_review
        """))
    command.downgrade(config, "20260916_0009")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT to_regclass('dedup_review')")) is None
        assert connection.scalar(sa.text("SELECT count(*) FROM source_listing")) == 2
    command.downgrade(config, "base")
    engine.dispose()
