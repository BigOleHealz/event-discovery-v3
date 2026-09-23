from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import psycopg
import pytest

from ingestion.canonicalization import CanonicalEventRepository, canonicalize_pending
from ingestion.database import IngestionRepository
from ingestion.eventbrite import eventbrite_location_slug
from ingestion.geocode_pipeline import geocode_pending
from ingestion.geocode_repository import GeocodeRepository
from ingestion.geocoding import GoogleGeocoder, GoogleGeocodingConfig
from ingestion.meetup import meetup_geo_radius
from ingestion.models import MeetupListingPage, MeetupSearchTarget
from ingestion.pipeline import (
    crawl_and_stage_meetup,
    group_crawl_targets_by_market,
    parse_and_filter_staged,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "meetup"
    / "event_search_page.json"
)
FROZEN_TIME = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
PHILADELPHIA_MARKET_ID = uuid.UUID("8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed")
GOOGLE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "google"
    / "geocode_city_hall.json"
)
pytestmark = pytest.mark.usefixtures("clean_ingestion_tables")


def psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def frozen_clock() -> datetime:
    return FROZEN_TIME


def recorded_event() -> dict[str, object]:
    body = json.loads(FIXTURE_PATH.read_text())
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        raise TypeError("recorded Meetup response must contain data")
    search = body["data"].get("eventSearch")
    if not isinstance(search, dict) or not isinstance(search.get("edges"), list):
        raise TypeError("recorded Meetup response must contain eventSearch edges")
    edge = search["edges"][0]
    if not isinstance(edge, dict) or not isinstance(edge.get("node"), dict):
        raise TypeError("recorded Meetup edge must contain a node")
    return cast(dict[str, object], edge["node"])


def insert_meetup_targets(database_url: str) -> None:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO ingest.crawl_target (
                id, source, market_id, source_location, category, enabled,
                window_days, page_cap
            ) VALUES
                (
                    'b8d299ce-bf03-4bc8-aa35-16f25fd95d34', 'meetup',
                    '8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed',
                    '{"kind":"meetup_geo_radius","lat":39.9526,"lon":-75.1652,
                      "radius_miles":25}'::jsonb,
                    '546', true, 5, 20
                ),
                (
                    '5e83bd87-ce71-48e7-b5d2-126bd69fe17a', 'meetup',
                    '8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed',
                    '{"kind":"meetup_geo_radius","lat":39.9526,"lon":-75.1652,
                      "radius_miles":25}'::jsonb,
                    '652', true, 5, 20
                )
            """
        )


def meetup_target(target_id: uuid.UUID, category: str) -> MeetupSearchTarget:
    return MeetupSearchTarget(
        crawl_target_id=target_id,
        latitude=39.9526,
        longitude=-75.1652,
        radius_miles=25,
        topic_category_id=category,
        window_start=datetime.fromisoformat("2026-10-06T00:00:00-04:00"),
        window_end=datetime.fromisoformat("2026-10-10T23:59:59-04:00"),
        page_cap=20,
    )


class RepeatingMeetupClient:
    def iter_event_pages(
        self, target: MeetupSearchTarget
    ) -> tuple[MeetupListingPage, ...]:
        return (
            MeetupListingPage(
                crawl_target_id=target.crawl_target_id,
                url="https://api.meetup.test/gql-ext",
                search_target=target.label,
                page_number=1,
                http_status=200,
                duration_ms=12,
                byte_count=1024,
                events=(recorded_event(),),
            ),
        )


def test_structurally_different_targets_share_market_but_group_per_source(
    database_url: str,
) -> None:
    insert_meetup_targets(database_url)
    repository = IngestionRepository(database_url)
    eventbrite_groups = group_crawl_targets_by_market(
        repository.enabled_crawl_targets(source="eventbrite")
    )
    meetup_groups = group_crawl_targets_by_market(
        repository.enabled_crawl_targets(source="meetup")
    )

    assert len(eventbrite_groups) == 1
    assert len(meetup_groups) == 1
    assert eventbrite_groups[0].market_id == meetup_groups[0].market_id
    assert eventbrite_groups[0].market_slug == meetup_groups[0].market_slug == "philadelphia-pa"
    assert eventbrite_groups[0].source == "eventbrite"
    assert meetup_groups[0].source == "meetup"
    assert eventbrite_location_slug(eventbrite_groups[0].targets[0].source_location) == (
        "pa--philadelphia"
    )
    meetup_location = meetup_geo_radius(meetup_groups[0].targets[0].source_location)
    assert (meetup_location.latitude, meetup_location.longitude, meetup_location.radius_miles) == (
        39.9526,
        -75.1652,
        25,
    )


def test_cross_category_meetup_event_is_staged_once_and_parseable(database_url: str) -> None:
    insert_meetup_targets(database_url)
    repository = IngestionRepository(database_url)
    run_id = repository.open_run(
        dag_id="ingest_meetup",
        airflow_run_id="scheduled__2026-08-28T03:00:00+00:00",
        source="meetup",
        source_url="https://api.meetup.test/gql-ext",
        market_id=PHILADELPHIA_MARKET_ID,
        categories=("546", "652"),
        window_start=FROZEN_TIME.date(),
        window_end=FROZEN_TIME.date(),
        started_at=FROZEN_TIME,
    )
    summary = crawl_and_stage_meetup(
        client=cast(object, RepeatingMeetupClient()),
        targets=(
            meetup_target(uuid.UUID("b8d299ce-bf03-4bc8-aa35-16f25fd95d34"), "546"),
            meetup_target(uuid.UUID("5e83bd87-ce71-48e7-b5d2-126bd69fe17a"), "652"),
        ),
        repository=repository,
        run_id=run_id,
        clock=frozen_clock,
    )
    parsed = parse_and_filter_staged(
        repository=repository,
        run_id=run_id,
        clock=frozen_clock,
        source="meetup",
    )

    assert summary.pages_fetched == 2
    assert summary.listing_appearances == 2
    assert summary.unique_ids == 1
    assert [listing.source_event_id for listing in parsed.accepted] == ["305912103"]
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM source_listing WHERE source = 'meetup'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM ingest.page_fetch WHERE run_id = %s", (run_id,)
        ).fetchone() == (2,)


def test_meetup_listing_reaches_its_own_canonical_event(database_url: str) -> None:
    insert_meetup_targets(database_url)
    repository = IngestionRepository(database_url)
    run_id = repository.open_run(
        dag_id="ingest_meetup",
        airflow_run_id="canonical-meetup-run",
        source="meetup",
        source_url="https://api.meetup.test/gql-ext",
        market_id=PHILADELPHIA_MARKET_ID,
        categories=("546",),
        window_start=FROZEN_TIME.date(),
        window_end=FROZEN_TIME.date(),
        started_at=FROZEN_TIME,
    )
    crawl_and_stage_meetup(
        client=cast(object, RepeatingMeetupClient()),
        targets=(
            meetup_target(uuid.UUID("b8d299ce-bf03-4bc8-aa35-16f25fd95d34"), "546"),
        ),
        repository=repository,
        run_id=run_id,
        clock=frozen_clock,
    )
    parsed = parse_and_filter_staged(
        repository=repository,
        run_id=run_id,
        clock=frozen_clock,
        source="meetup",
    )
    assert len(parsed.accepted) == 1

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=GOOGLE_FIXTURE_PATH.read_bytes(), request=request)

    geocoding_config = GoogleGeocodingConfig(
        api_key="recorded-google-test-key",
        api_base_url="https://geocode.test/v4/geocode/address",
        request_timeout_seconds=5,
        region_code="US",
        language_code="en",
    )
    with GoogleGeocoder(
        geocoding_config, transport=httpx.MockTransport(handler)
    ) as geocoder:
        geocode_summary = geocode_pending(
            repository=GeocodeRepository(database_url),
            ingestion_repository=repository,
            geocoder=geocoder,
            clock=frozen_clock,
        )
    canonical_summary = canonicalize_pending(
        repository=CanonicalEventRepository(database_url),
        clock=frozen_clock,
    )

    assert geocode_summary.api_calls == 1
    assert canonical_summary.created == 1
    with psycopg.connect(psycopg_url(database_url)) as connection:
        row = connection.execute(
            """
            SELECT event.title, event.timezone, listing.source
            FROM canonical_event AS event
            JOIN source_listing AS listing ON listing.canonical_event_id = event.id
            """
        ).fetchone()
    assert row == ("Go Hard", "America/New_York", "meetup")
