from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import psycopg
import pytest

from ingestion.canonicalization import CanonicalEventRepository, canonicalize_pending
from ingestion.database import IngestionRepository
from ingestion.geocode_pipeline import geocode_pending
from ingestion.geocode_repository import GeocodeRepository
from ingestion.geocoding import GoogleGeocoder, GoogleGeocodingConfig
from ingestion.models import FetchedPage
from ingestion.pipeline import fetch_and_stage, parse_and_filter_staged

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EVENTBRITE_FIXTURE_PATH = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "eventbrite" / "organization_events_page_1.json"
)
GOOGLE_FIXTURE_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "google" / "geocode_city_hall.json"
FIRST_TIME = datetime(2026, 8, 27, 19, 0, tzinfo=UTC)
SECOND_TIME = FIRST_TIME + timedelta(hours=1)
pytestmark = pytest.mark.usefixtures("clean_ingestion_tables")


def psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def recorded_event() -> dict[str, object]:
    value = json.loads(EVENTBRITE_FIXTURE_PATH.read_text())
    if not isinstance(value, dict):
        raise TypeError("recorded Eventbrite page must be an object")
    events = value.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        raise TypeError("recorded Eventbrite page must contain an event")
    return cast(dict[str, object], events[0])


def event_variant(source_event_id: str, title: str) -> dict[str, object]:
    event = deepcopy(recorded_event())
    event["id"] = source_event_id
    event["url"] = f"https://www.eventbrite.com/e/{source_event_id}"
    name = event.get("name")
    if not isinstance(name, dict):
        raise TypeError("recorded Eventbrite name must be an object")
    name["text"] = title
    return event


def event_with_address(source_event_id: str, title: str, address_text: str) -> dict[str, object]:
    event = event_variant(source_event_id, title)
    venue = event.get("venue")
    if not isinstance(venue, dict):
        raise TypeError("recorded Eventbrite venue must be an object")
    address = venue.get("address")
    if not isinstance(address, dict):
        raise TypeError("recorded Eventbrite address must be an object")
    address["localized_address_display"] = address_text
    return event


def frozen_clock(value: datetime):
    def current_time() -> datetime:
        return value

    return current_time


def stage_run(
    database_url: str,
    *,
    events: tuple[dict[str, object], ...],
    observed_at: datetime,
) -> uuid.UUID:
    repository = IngestionRepository(database_url)
    run_id = repository.open_run(
        dag_id="ingest_eventbrite",
        airflow_run_id="scheduled__2026-08-27T03:15:00+00:00",
        source="eventbrite",
        source_url="https://eventbrite.test/v3/organizations/recorded/events/",
        market_id=uuid.UUID("8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed"),
        started_at=observed_at,
    )
    found = fetch_and_stage(
        pages=(
            FetchedPage(
                url="https://eventbrite.test/v3/organizations/recorded/events/?page=1",
                http_status=200,
                duration_ms=17,
                byte_count=2048,
                events=events,
            ),
        ),
        repository=repository,
        run_id=run_id,
        clock=frozen_clock(observed_at),
    )
    parsed = parse_and_filter_staged(
        repository=repository,
        run_id=run_id,
        clock=frozen_clock(observed_at),
    )
    assert len(parsed.accepted) == len(events)
    repository.mark_success(
        run_id=run_id,
        events_found=found,
        events_rejected_online=parsed.rejected_online,
        finished_at=observed_at,
    )
    return run_id


def google_config() -> GoogleGeocodingConfig:
    return GoogleGeocodingConfig(
        api_key="recorded-google-test-key",
        api_base_url="https://geocode.test/v4/geocode/address",
        request_timeout_seconds=5,
        region_code="US",
        language_code="en",
    )


def scalar(database_url: str, query: str) -> object:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        row = connection.execute(query).fetchone()
    if row is None:
        raise AssertionError("query returned no row")
    return row[0]


def test_full_pipeline_rerun_updates_without_duplicate_rows(database_url: str) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            content=GOOGLE_FIXTURE_PATH.read_bytes(),
            request=request,
        )

    ingestion_repository = IngestionRepository(database_url)
    geocode_repository = GeocodeRepository(database_url)
    canonical_repository = CanonicalEventRepository(database_url)
    summaries = []
    geocode_summaries = []
    with GoogleGeocoder(google_config(), transport=httpx.MockTransport(handler)) as geocoder:
        for observed_at, title in (
            (FIRST_TIME, "Go Hard"),
            (SECOND_TIME, "Go Hard — Updated"),
        ):
            stage_run(
                database_url,
                events=(event_variant("1999042913166", title),),
                observed_at=observed_at,
            )
            geocode_summaries.append(
                geocode_pending(
                    repository=geocode_repository,
                    ingestion_repository=ingestion_repository,
                    geocoder=geocoder,
                    clock=frozen_clock(observed_at),
                )
            )
            summaries.append(
                canonicalize_pending(
                    repository=canonical_repository,
                    clock=frozen_clock(observed_at),
                )
            )

    assert request_count == 1
    assert geocode_summaries[0].api_calls == 1
    assert geocode_summaries[1].cache_hits == 1
    assert summaries[0].created == 1
    assert summaries[0].updated == 0
    assert summaries[1].created == 0
    assert summaries[1].updated == 1
    assert scalar(database_url, "SELECT count(*) FROM venue") == 1
    assert scalar(database_url, "SELECT count(*) FROM canonical_event") == 1
    assert scalar(database_url, "SELECT count(*) FROM source_listing") == 1
    assert scalar(database_url, "SELECT title FROM canonical_event") == "Go Hard — Updated"
    assert scalar(database_url, "SELECT created_at FROM canonical_event") == FIRST_TIME
    assert scalar(database_url, "SELECT updated_at FROM canonical_event") == SECOND_TIME
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM source_listing WHERE canonical_event_id IS NOT NULL",
        )
        == 1
    )
    assert scalar(database_url, "SELECT events_new FROM ingest.run") == 0
    assert scalar(database_url, "SELECT events_updated FROM ingest.run") == 1
    assert scalar(database_url, "SELECT registration_url FROM source_listing") == (
        "https://www.eventbrite.com/e/1999042913166"
    )


def test_two_listings_at_one_venue_create_two_canonical_events(database_url: str) -> None:
    events = (
        event_variant("event-one", "First City Hall Event"),
        event_variant("event-two", "Second City Hall Event"),
    )
    run_id = stage_run(database_url, events=events, observed_at=FIRST_TIME)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=GOOGLE_FIXTURE_PATH.read_bytes(),
            request=request,
        )

    with GoogleGeocoder(google_config(), transport=httpx.MockTransport(handler)) as geocoder:
        geocode_summary = geocode_pending(
            repository=GeocodeRepository(database_url),
            ingestion_repository=IngestionRepository(database_url),
            geocoder=geocoder,
            clock=frozen_clock(FIRST_TIME),
        )
    summary = canonicalize_pending(
        repository=CanonicalEventRepository(database_url),
        clock=frozen_clock(FIRST_TIME),
    )

    assert geocode_summary.unique_addresses == 1
    assert geocode_summary.api_calls == 1
    assert summary.created == 2
    assert scalar(database_url, "SELECT count(*) FROM venue") == 1
    assert scalar(database_url, "SELECT count(*) FROM canonical_event") == 2
    assert (
        scalar(
            database_url,
            "SELECT count(DISTINCT canonical_event_id) FROM source_listing",
        )
        == 2
    )
    with psycopg.connect(psycopg_url(database_url)) as connection:
        counts = connection.execute(
            "SELECT events_new, events_updated FROM ingest.run WHERE id = %s",
            (run_id,),
        ).fetchone()
    assert counts == (2, 0)


def test_canonicalization_waits_for_geocode_cache(database_url: str) -> None:
    stage_run(
        database_url,
        events=(event_variant("waiting-for-geocode", "Waiting"),),
        observed_at=FIRST_TIME,
    )

    summary = canonicalize_pending(
        repository=CanonicalEventRepository(database_url),
        clock=frozen_clock(FIRST_TIME),
    )

    assert summary.candidates == 1
    assert summary.awaiting_geocode == 1
    assert summary.created == 0
    assert scalar(database_url, "SELECT count(*) FROM canonical_event") == 0
    assert scalar(database_url, "SELECT canonical_event_id FROM source_listing") is None


def test_ungeocodable_update_archives_event_without_rebilling(database_url: str) -> None:
    stage_run(
        database_url,
        events=(event_variant("moves-venue", "Original Venue"),),
        observed_at=FIRST_TIME,
    )

    def recorded_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=GOOGLE_FIXTURE_PATH.read_bytes(),
            request=request,
        )

    with GoogleGeocoder(
        google_config(), transport=httpx.MockTransport(recorded_handler)
    ) as geocoder:
        geocode_pending(
            repository=GeocodeRepository(database_url),
            ingestion_repository=IngestionRepository(database_url),
            geocoder=geocoder,
            clock=frozen_clock(FIRST_TIME),
        )
    canonicalize_pending(
        repository=CanonicalEventRepository(database_url),
        clock=frozen_clock(FIRST_TIME),
    )

    stage_run(
        database_url,
        events=(
            event_with_address(
                "moves-venue",
                "Moved Venue",
                "999999 Imaginary Avenue, Philadelphia, PA 19199",
            ),
        ),
        observed_at=SECOND_TIME,
    )
    request_count = 0

    def not_found_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"results": []}, request=request)

    with GoogleGeocoder(
        google_config(), transport=httpx.MockTransport(not_found_handler)
    ) as geocoder:
        first = geocode_pending(
            repository=GeocodeRepository(database_url),
            ingestion_repository=IngestionRepository(database_url),
            geocoder=geocoder,
            clock=frozen_clock(SECOND_TIME),
        )
        second = geocode_pending(
            repository=GeocodeRepository(database_url),
            ingestion_repository=IngestionRepository(database_url),
            geocoder=geocoder,
            clock=frozen_clock(SECOND_TIME),
        )

    assert request_count == 1
    assert first.rejected_no_location == 1
    assert second.pending_listings == 0
    assert scalar(database_url, "SELECT count(*) FROM source_listing") == 1
    assert scalar(database_url, "SELECT count(*) FROM canonical_event") == 1
    assert scalar(database_url, "SELECT archived_at FROM canonical_event") == SECOND_TIME
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM ingest.rejected_listing WHERE reason = 'no_location'",
        )
        == 1
    )
