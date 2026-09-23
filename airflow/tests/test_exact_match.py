from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from ingestion.canonicalization import (
    CanonicalEventRepository,
    CanonicalizationCandidate,
    normalize_title,
)
from ingestion.models import ParsedListing

PHILADELPHIA_MARKET_ID = uuid.UUID("8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed")
FIRST_SEEN = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)
STARTS_AT = datetime(2026, 10, 6, 14, 0, 15, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("clean_ingestion_tables")


def psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def insert_run(database_url: str, source: str) -> uuid.UUID:
    run_id = uuid.uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO ingest.run (
                id, run_date, source, source_url, market_id, started_at, status
            ) VALUES (
                %(id)s, %(run_date)s, %(source)s, %(source_url)s,
                %(market_id)s, %(started_at)s, 'success'
            )
            """,
            {
                "id": run_id,
                "run_date": FIRST_SEEN.date(),
                "source": source,
                "source_url": f"https://{source}.test/events",
                "market_id": PHILADELPHIA_MARKET_ID,
                "started_at": FIRST_SEEN,
            },
        )
    return run_id


def insert_venue(database_url: str, google_place_id: str) -> uuid.UUID:
    venue_id = uuid.uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO venue (
                id, name, formatted_address, google_place_id, location,
                city, region, country
            ) VALUES (
                %(id)s, 'World Cafe Live', '3025 Walnut Street, Philadelphia, PA',
                %(google_place_id)s,
                ST_SetSRID(ST_MakePoint(-75.1850, 39.9522), 4326)::geography,
                'Philadelphia', 'PA', 'US'
            )
            """,
            {"id": venue_id, "google_place_id": google_place_id},
        )
    return venue_id


def candidate(
    database_url: str,
    *,
    source: str,
    source_event_id: str,
    title: str,
    starts_at: datetime,
) -> CanonicalizationCandidate:
    run_id = insert_run(database_url, source)
    listing_id = uuid.uuid4()
    url = f"https://{source}.test/events/{source_event_id}"
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO source_listing (
                id, source, source_event_id, url, registration_url, raw_payload,
                ingestion_run_id, first_seen_at, last_seen_at
            ) VALUES (
                %(id)s, %(source)s, %(source_event_id)s, %(url)s, %(url)s,
                '{}'::jsonb, %(run_id)s, %(seen_at)s, %(seen_at)s
            )
            """,
            {
                "id": listing_id,
                "source": source,
                "source_event_id": source_event_id,
                "url": url,
                "run_id": run_id,
                "seen_at": FIRST_SEEN,
            },
        )
    listing = ParsedListing(
        source_event_id=source_event_id,
        url=url,
        title=title,
        description="Recorded listing for exact-match tests",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=2),
        timezone="America/New_York",
        online_event=False,
        venue_name="World Cafe Live",
        venue_address="3025 Walnut Street, Philadelphia, PA",
        venue_city="Philadelphia",
        venue_region="PA",
        venue_country="US",
        latitude=39.9522,
        longitude=-75.1850,
        primary_category="Music",
    )
    return CanonicalizationCandidate(
        listing_id=listing_id,
        ingestion_run_id=run_id,
        source=source,
        listing=listing,
        normalized_address="3025 walnut street philadelphia pa",
    )


def test_title_normalization_is_case_punctuation_and_width_insensitive() -> None:
    assert normalize_title("  ＧＯ—Hard!!!  ") == "go hard"
    assert normalize_title("Café's Late Set") == "café s late set"


@pytest.mark.parametrize(
    ("second_title", "second_start", "same_place", "expected_action"),
    [
        (
            "  ＧＯ—HARD!!! ",
            datetime.fromisoformat("2026-10-06T10:00:45-04:00"),
            True,
            "deduped",
        ),
        ("Go Soft", STARTS_AT, True, "created"),
        ("Go Hard", STARTS_AT + timedelta(minutes=1), True, "created"),
        ("Go Hard", STARTS_AT, False, "created"),
    ],
)
def test_exact_match_requires_place_start_minute_and_normalized_title(
    database_url: str,
    second_title: str,
    second_start: datetime,
    same_place: bool,
    expected_action: str,
) -> None:
    repository = CanonicalEventRepository(database_url)
    first_venue_id = insert_venue(database_url, "place-world-cafe-live")
    second_venue_id = (
        first_venue_id
        if same_place
        else insert_venue(database_url, "place-another-philadelphia-venue")
    )
    eventbrite = candidate(
        database_url,
        source="eventbrite",
        source_event_id="eventbrite-100",
        title="Go Hard",
        starts_at=STARTS_AT,
    )
    meetup = candidate(
        database_url,
        source="meetup",
        source_event_id="meetup-200",
        title=second_title,
        starts_at=second_start,
    )

    first_action = repository.upsert(
        candidate=eventbrite,
        venue_id=first_venue_id,
        written_at=FIRST_SEEN,
    )
    second_action = repository.upsert(
        candidate=meetup,
        venue_id=second_venue_id,
        written_at=FIRST_SEEN + timedelta(seconds=1),
    )

    expected_events = 1 if expected_action == "deduped" else 2
    assert first_action == "created"
    assert second_action == expected_action
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute("SELECT count(*) FROM canonical_event").fetchone() == (
            expected_events,
        )
        canonical_ids = connection.execute(
            """
            SELECT source, canonical_event_id
            FROM source_listing
            ORDER BY source
            """
        ).fetchall()
        meetup_counts = connection.execute(
            """
            SELECT events_new, events_updated, events_deduped
            FROM ingest.run
            WHERE id = %(run_id)s
            """,
            {"run_id": meetup.ingestion_run_id},
        ).fetchone()

    if expected_action == "deduped":
        assert canonical_ids[0][1] == canonical_ids[1][1]
        assert meetup_counts == (0, 0, 1)
    else:
        assert canonical_ids[0][1] != canonical_ids[1][1]
        assert meetup_counts == (1, 0, 0)
