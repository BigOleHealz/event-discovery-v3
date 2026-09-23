from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from test_embeddings import config
from test_exact_match import (
    FIRST_SEEN,
    STARTS_AT,
    candidate,
    insert_run,
    insert_venue,
    psycopg_url,
)

from ingestion.canonicalization import CanonicalEventRepository
from ingestion.dedup import CANDIDATE_SQL, classify, dedup_pending
from ingestion.embeddings import EmbeddingClient, embed_pending

CORPUS = json.loads(
    (Path(__file__).resolve().parents[2] / "tests/fixtures/dedup/pairs.json").read_text()
)
pytestmark = pytest.mark.usefixtures("clean_ingestion_tables")


def insert_listing(
    database_url: str,
    data: dict,
    starts_at: datetime,
    *,
    state: str = "pending",
) -> uuid.UUID:
    listing_id = uuid.uuid4()
    run_id = insert_run(database_url, data["source"])
    prefix = data["embedding_prefix"]
    embedding = json.dumps(prefix + [0] * (1536 - len(prefix)))
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """INSERT INTO canonical_event (
                id, title, description, starts_at, timezone, location, recurrence_group_id
            ) VALUES (%s, %s, %s, %s, 'America/New_York',
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)""",
            (
                listing_id,
                data["title"],
                data["description"],
                starts_at,
                data["longitude"],
                data["latitude"],
                uuid.UUID(int=1),
            ),
        )
        connection.execute(
            """INSERT INTO source_listing (
                id, canonical_event_id, source, source_event_id, url, raw_payload,
                ingestion_run_id, embedding, dedup_state, first_seen_at, last_seen_at
            ) VALUES (%s, %s, %s, %s, 'https://fixture.test', '{}', %s, %s::vector,
                %s, %s, %s)""",
            (
                listing_id,
                listing_id,
                data["source"],
                str(listing_id),
                run_id,
                embedding,
                state,
                FIRST_SEEN,
                FIRST_SEEN,
            ),
        )
    return listing_id


def seed_pair(database_url: str, pair: dict) -> tuple[uuid.UUID, uuid.UUID]:
    start = datetime.fromisoformat(pair["a"]["starts_at"])
    first = insert_listing(database_url, pair["a"], start, state="distinct")
    second = insert_listing(
        database_url, pair["b"], start + timedelta(minutes=pair["b"]["minutes_after"])
    )
    return first, second


@pytest.mark.parametrize("pair", CORPUS, ids=lambda pair: pair["id"])
def test_labelled_classification_and_retry(database_url: str, pair: dict) -> None:
    first, second = seed_pair(database_url, pair)
    counts = dedup_pending(database_url, clock=lambda: FIRST_SEEN)
    assert counts[pair["expected"]] == 1
    assert dedup_pending(database_url, clock=lambda: FIRST_SEEN) == {
        "same": 0,
        "review": 0,
        "distinct": 0,
    }
    with psycopg.connect(psycopg_url(database_url)) as connection:
        state, matched = connection.execute(
            "SELECT dedup_state, dedup_match_id FROM source_listing WHERE id = %s", (second,)
        ).fetchone()
        assert state == pair["expected"]
        events = connection.execute("SELECT id, title FROM canonical_event").fetchall()
        if state == "same":
            assert events == [(first, pair["a"]["title"])]
            assert connection.execute(
                "SELECT count(DISTINCT canonical_event_id) FROM source_listing"
            ).fetchone() == (1,)
        else:
            assert len(events) == 2
            assert connection.execute(
                "SELECT count(DISTINCT canonical_event_id) FROM source_listing"
            ).fetchone() == (2,)
        if state == "review":
            assert matched == first
            review = connection.execute("""
                SELECT listing_a_id, listing_b_id, status, created_at FROM dedup_review
            """).fetchall()
            assert review == [(*sorted((first, second)), "pending", FIRST_SEEN)]
        else:
            assert connection.execute("SELECT count(*) FROM dedup_review").fetchone() == (0,)


@pytest.mark.parametrize("status", ["pending", "distinct", "skipped"])
def test_unmerged_review_survives_automatic_reprocessing(database_url: str, status: str) -> None:
    pair = next(pair for pair in CORPUS if pair["expected"] == "review")
    first, second = seed_pair(database_url, pair)
    dedup_pending(database_url, clock=lambda: FIRST_SEEN)
    reviewer = uuid.uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            "INSERT INTO app_user (id, is_shadow) VALUES (%s, true)", (reviewer,)
        )
        if status != "pending":
            connection.execute("""
                UPDATE dedup_review SET status = %s, decided_by = %s, decided_at = %s
            """, (status, reviewer, FIRST_SEEN))
        # Even an identical new embedding cannot bypass the review queue.
        connection.execute("""
            UPDATE source_listing SET dedup_state = 'pending',
                embedding = (SELECT embedding FROM source_listing WHERE id = %s)
            WHERE id = %s
        """, (first, second))
    assert dedup_pending(database_url, clock=lambda: FIRST_SEEN)["distinct"] == 1
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute("""
            SELECT count(DISTINCT canonical_event_id) FROM source_listing
        """).fetchone() == (2,)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.88, "same"),
        (0.75, "review"),
        (0.749, "distinct"),
        (1.0, "same"),
        (0.0, "distinct"),
        (float("nan"), "distinct"),
    ],
)
def test_similarity_threshold_direction(value: float, expected: str) -> None:
    assert classify(value) == expected


def test_hard_filter_precedes_cosine(database_url: str) -> None:
    pair = CORPUS[1]
    first, second = seed_pair(database_url, pair)
    start = datetime.fromisoformat(pair["a"]["starts_at"])
    # More globally identical distractors than the query's LIMIT, all ineligible.
    for i in range(12):
        data = dict(
            pair["a"], longitude=-73.985 if i % 2 else -75.185,
            embedding_prefix=[1, 0, (i + 1) / 1000],
        )
        insert_listing(database_url, data, start + timedelta(days=7 if i % 2 == 0 else 0))
    with psycopg.connect(psycopg_url(database_url)) as connection:
        # Instrument the actual SQL operator, keeping real pgvector computation.
        # Rollback removes the probe schema and its function/operator after the test.
        connection.execute("""
            CREATE SCHEMA dedup_probe;
            CREATE TEMP TABLE cosine_calls (operand vector);
            CREATE FUNCTION dedup_probe.distance(vector, vector) RETURNS float8
            LANGUAGE plpgsql VOLATILE AS $$
            BEGIN
                INSERT INTO cosine_calls VALUES ($1);
                RETURN public.cosine_distance($1, $2);
            END $$;
            CREATE OPERATOR dedup_probe.<=> (
                LEFTARG = vector, RIGHTARG = vector, FUNCTION = dedup_probe.distance
            );
            SET LOCAL search_path = dedup_probe, public;
        """)
        matches = connection.execute(CANDIDATE_SQL, {"listing_id": second}).fetchall()
        assert [row[0] for row in matches] == [first]
        # The planner may reuse the distance for SELECT/ORDER BY. Every evaluated
        # operand must be eligible, regardless of how often it is evaluated.
        assert connection.execute("SELECT count(*) > 0 FROM cosine_calls").fetchone() == (True,)
        assert connection.execute(
            """SELECT count(*) FROM cosine_calls
               WHERE operand <> (SELECT embedding FROM source_listing WHERE id = %s)""",
            (first,),
        ).fetchone() == (0,)
        connection.rollback()


def test_exact_match_never_requests_embedding(database_url: str) -> None:
    venue_id = insert_venue(database_url, "exact-place")
    repository = CanonicalEventRepository(database_url)
    for source in ("eventbrite", "meetup"):
        item = candidate(
            database_url,
            source=source,
            source_event_id=source,
            title="Go Hard",
            starts_at=STARTS_AT,
        )
        repository.upsert(candidate=item, venue_id=venue_id, written_at=FIRST_SEEN)
        if source == "eventbrite":
            with psycopg.connect(psycopg_url(database_url)) as connection:
                connection.execute(
                    "UPDATE source_listing SET embedding = %s::vector WHERE id = %s",
                    (json.dumps([1] + [0] * 1535), item.listing_id),
                )

    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("Exact-match shortcut requested an embedding")

    with EmbeddingClient(config(), transport=httpx.MockTransport(forbidden)) as client:
        assert embed_pending(database_url, client) == 0
        assert embed_pending(database_url, client) == 0
    dedup_pending(database_url, clock=lambda: FIRST_SEEN)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT dedup_state, embedding FROM source_listing WHERE source = 'meetup'"
        ).fetchone() == ("exact", None)
        assert connection.execute("SELECT count(*) FROM canonical_event").fetchone() == (1,)


def test_missing_embedding_waits(database_url: str) -> None:
    _, second = seed_pair(database_url, CORPUS[0])
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute("UPDATE source_listing SET embedding = NULL WHERE id = %s", (second,))
    assert sum(dedup_pending(database_url, clock=lambda: FIRST_SEEN).values()) == 0
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT dedup_state FROM source_listing WHERE id = %s", (second,)
        ).fetchone() == ("pending",)


def test_source_priority_is_configurable(database_url: str) -> None:
    _, second = seed_pair(database_url, CORPUS[0])
    dedup_pending(database_url, clock=lambda: FIRST_SEEN, source_priority=("meetup", "eventbrite"))
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute("SELECT id FROM canonical_event").fetchall() == [(second,)]


@pytest.mark.parametrize("minutes,expected", [(-91, "distinct"), (-90, "same")])
def test_occurrence_window_in_both_directions(
    database_url: str, minutes: int, expected: str,
) -> None:
    pair = CORPUS[0]
    seed_pair(database_url, {**pair, "b": {**pair["b"], "minutes_after": minutes}})
    assert dedup_pending(database_url, clock=lambda: FIRST_SEEN)[expected] == 1


@pytest.mark.parametrize("meters,expected", [(499, "same"), (501, "distinct")])
def test_geography_radius_in_meters(database_url: str, meters: int, expected: str) -> None:
    first, second = seed_pair(database_url, CORPUS[0])
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """UPDATE canonical_event SET location = ST_Project(
                   (SELECT location FROM canonical_event WHERE id = %s), %s, 0)
               WHERE id = %s""", (first, meters, second),
        )
    assert dedup_pending(database_url, clock=lambda: FIRST_SEEN)[expected] == 1


def test_best_candidate_uses_smallest_cosine_distance(database_url: str) -> None:
    pair = CORPUS[0]
    first, second = seed_pair(database_url, pair)
    start = datetime.fromisoformat(pair["a"]["starts_at"])
    for prefix in ([0, 1], [-1, 0], [0.6, 0.8]):
        insert_listing(
            database_url, {**pair["a"], "embedding_prefix": prefix}, start, state="distinct",
        )
    assert dedup_pending(database_url, clock=lambda: FIRST_SEEN)["same"] == 1
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT canonical_event_id, dedup_match_id FROM source_listing WHERE id = %s",
            (second,),
        ).fetchone() == (first, first)


def test_failure_rolls_back_merge_and_retry_counts_once(database_url: str) -> None:
    first, second = seed_pair(database_url, CORPUS[0])

    def failed_clock() -> datetime:
        raise RuntimeError("interrupted before outcome was saved")

    with pytest.raises(RuntimeError, match="interrupted"):
        dedup_pending(database_url, clock=failed_clock)
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute("SELECT count(*) FROM canonical_event").fetchone() == (2,)
        assert connection.execute(
            "SELECT canonical_event_id, dedup_state FROM source_listing WHERE id = %s",
            (second,),
        ).fetchone() == (second, "pending")
        assert connection.execute(
            "SELECT COALESCE(sum(events_deduped), 0) FROM ingest.run"
        ).fetchone() == (0,)
    assert dedup_pending(database_url, clock=lambda: FIRST_SEEN)["same"] == 1
    assert sum(dedup_pending(database_url, clock=lambda: FIRST_SEEN).values()) == 0
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute("SELECT id FROM canonical_event").fetchall() == [(first,)]
        assert connection.execute(
            "SELECT sum(events_deduped) FROM ingest.run"
        ).fetchone() == (1,)
