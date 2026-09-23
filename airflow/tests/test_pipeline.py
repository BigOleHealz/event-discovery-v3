from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import psycopg
import pytest

from ingestion.database import IngestionRepository
from ingestion.eventbrite import EventbriteParseError
from ingestion.models import FetchedPage
from ingestion.pipeline import (
    fail_run_on_error,
    fetch_and_stage,
    parse_and_filter_staged,
    parse_staged,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "eventbrite"
    / "organization_events_page_1.json"
)
FROZEN_TIME = datetime(2026, 8, 27, 18, 30, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("clean_ingestion_tables")


def frozen_clock() -> datetime:
    return FROZEN_TIME


def psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def recorded_event() -> dict[str, object]:
    page = json.loads(FIXTURE_PATH.read_text())
    if not isinstance(page, dict):
        raise TypeError("recorded Eventbrite page must be an object")
    events = page.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        raise TypeError("recorded Eventbrite page must contain an event")
    return cast(dict[str, object], events[0])


def fetched_page(event: dict[str, object]) -> FetchedPage:
    return FetchedPage(
        url="https://eventbrite.test/v3/organizations/recorded/events/?page=1",
        http_status=200,
        duration_ms=17,
        byte_count=2048,
        events=(event,),
    )


def event_variant(
    source_event_id: str,
    *,
    online_event: bool = False,
    venue_name: str | None = "Philadelphia City Hall",
    venue_address: str | None = "1400 John F Kennedy Boulevard, Philadelphia, PA 19107",
    title: str = "Go Hard",
    description: str = "yo yo yo",
) -> dict[str, object]:
    event = deepcopy(recorded_event())
    event["id"] = source_event_id
    event["url"] = f"https://eventbrite.test/e/{source_event_id}"
    event["online_event"] = online_event
    name = event["name"]
    if not isinstance(name, dict):
        raise TypeError("fixture name must be an object")
    name["text"] = title
    details = event["description"]
    if not isinstance(details, dict):
        raise TypeError("fixture description must be an object")
    details["text"] = description
    venue = event["venue"]
    if not isinstance(venue, dict):
        raise TypeError("fixture venue must be an object")
    venue["name"] = venue_name
    address = venue["address"]
    if not isinstance(address, dict):
        raise TypeError("fixture address must be an object")
    address["localized_address_display"] = venue_address
    if venue_address is None or venue_address == "To be announced":
        address["latitude"] = None
        address["longitude"] = None
    return event


def open_run(repository: IngestionRepository) -> uuid.UUID:
    return repository.open_run(
        dag_id="ingest_eventbrite",
        airflow_run_id="scheduled__2026-08-27T03:15:00+00:00",
        source="eventbrite",
        source_url="https://eventbrite.test/v3/organizations/recorded/events/",
        market_id=uuid.UUID("8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed"),
        started_at=FROZEN_TIME,
    )


def scalar(database_url: str, query: str) -> object:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        row = connection.execute(query).fetchone()
    if row is None:
        raise AssertionError("query returned no row")
    return row[0]


def test_staging_and_success_lifecycle_are_idempotent(database_url: str) -> None:
    repository = IngestionRepository(database_url)
    page = fetched_page(recorded_event())

    for _ in range(2):
        run_id = open_run(repository)
        found = fetch_and_stage(
            pages=(page,), repository=repository, run_id=run_id, clock=frozen_clock
        )
        parsed = parse_staged(repository=repository, run_id=run_id)
        repository.mark_success(run_id=run_id, events_found=found, finished_at=frozen_clock())
        assert len(parsed) == 1

    assert scalar(database_url, "SELECT count(*) FROM ingest.run") == 1
    assert scalar(database_url, "SELECT count(*) FROM ingest.page_fetch") == 1
    assert scalar(database_url, "SELECT count(*) FROM source_listing") == 1
    assert scalar(database_url, "SELECT status FROM ingest.run") == "success"
    assert scalar(database_url, "SELECT events_found FROM ingest.run") == 1
    assert scalar(database_url, "SELECT finished_at FROM ingest.run") == FROZEN_TIME


def test_raw_payload_survives_parser_failure_and_run_is_closed(database_url: str) -> None:
    repository = IngestionRepository(database_url)
    malformed = recorded_event()
    malformed.pop("name")
    run_id = open_run(repository)
    fetch_and_stage(
        pages=(fetched_page(malformed),),
        repository=repository,
        run_id=run_id,
        clock=frozen_clock,
    )

    with pytest.raises(EventbriteParseError, match="name must be an object"):
        with fail_run_on_error(repository=repository, run_id=run_id, clock=frozen_clock):
            parse_staged(repository=repository, run_id=run_id)

    assert scalar(database_url, "SELECT count(*) FROM source_listing") == 1
    assert scalar(database_url, "SELECT raw_payload->>'id' FROM source_listing") == (
        "1999042913166"
    )
    assert scalar(database_url, "SELECT status FROM ingest.run") == "failed"
    assert scalar(database_url, "SELECT finished_at FROM ingest.run") == FROZEN_TIME
    error_message = scalar(database_url, "SELECT error_message FROM ingest.run")
    assert isinstance(error_message, str)
    assert "name must be an object" in error_message


def test_filter_rejections_are_logged_and_idempotent(database_url: str) -> None:
    repository = IngestionRepository(database_url)
    page = FetchedPage(
        url="https://eventbrite.test/v3/organizations/recorded/events/?page=1",
        http_status=200,
        duration_ms=17,
        byte_count=8192,
        events=(
            event_variant("online-flag", online_event=True),
            event_variant("virtual-venue", venue_name="Online webinar"),
            event_variant(
                "virtual-content",
                venue_name=None,
                venue_address=None,
                title="Remote community meetup",
            ),
            event_variant(
                "missing-location",
                venue_name=None,
                venue_address="To be announced",
            ),
            event_variant(
                "hybrid",
                description="Join us at City Hall; a livestream is also available.",
            ),
        ),
    )

    for _ in range(2):
        run_id = open_run(repository)
        found = fetch_and_stage(
            pages=(page,), repository=repository, run_id=run_id, clock=frozen_clock
        )
        summary = parse_and_filter_staged(repository=repository, run_id=run_id, clock=frozen_clock)
        repository.mark_success(
            run_id=run_id,
            events_found=found,
            events_rejected_online=summary.rejected_online,
            finished_at=frozen_clock(),
        )
        assert len(summary.accepted) == 1
        assert summary.accepted[0].source_event_id == "hybrid"
        assert summary.rejected_online == 3
        assert summary.rejected_no_location == 1
        assert summary.processed_total == 5

    assert scalar(database_url, "SELECT count(*) FROM ingest.rejected_listing") == 4
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM ingest.rejected_listing WHERE reason = 'online'",
        )
        == 3
    )
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM ingest.rejected_listing WHERE reason = 'no_location'",
        )
        == 1
    )
    assert scalar(database_url, "SELECT count(*) FROM source_listing") == 1
    assert scalar(database_url, "SELECT events_rejected_online FROM ingest.run") == 3
