from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import psycopg
import pytest

from ingestion.database import IngestionRepository
from ingestion.eventbrite import EventbriteRateLimited
from ingestion.models import (
    EventbriteEventReference,
    EventbriteListingPage,
    EventbriteSearchTarget,
)
from ingestion.pipeline import (
    crawl_eventbrite_location,
    fetch_and_stage_eventbrite_details,
    group_crawl_targets_by_market,
)

DETAIL_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "eventbrite"
    / "event_detail_1999042913166.json"
)
FROZEN_TIME = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("clean_ingestion_tables")


def frozen_clock() -> datetime:
    return FROZEN_TIME


def psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def detail(event_id: str = "1999042913166") -> dict[str, object]:
    value = json.loads(DETAIL_FIXTURE_PATH.read_text())
    if not isinstance(value, dict):
        raise TypeError("recorded detail must be an object")
    payload = deepcopy(cast(dict[str, object], value))
    payload["id"] = event_id
    payload["url"] = f"https://www.eventbrite.com/e/test-tickets-{event_id}"
    return payload


def target(category: str) -> EventbriteSearchTarget:
    target_ids = {
        "science-and-tech": uuid.UUID("4c33ed98-a96b-4d72-946f-5bd923db9506"),
        "food-and-drink": uuid.UUID("a8ddad4f-d5af-4f24-a8a6-99e10aa3d76f"),
    }
    return EventbriteSearchTarget(
        crawl_target_id=target_ids[category],
        location_slug="pa--philadelphia",
        category=category,
        window_start=date(2026, 8, 27),
        window_end=date(2026, 8, 31),
        page_cap=20,
    )


def open_run(repository: IngestionRepository, airflow_run_id: str) -> uuid.UUID:
    return repository.open_run(
        dag_id="ingest_eventbrite",
        airflow_run_id=airflow_run_id,
        source_url="https://www.eventbrite.com/d/pa--philadelphia/",
        market_id=uuid.UUID("8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed"),
        city="pa--philadelphia",
        categories=("science-and-tech", "food-and-drink"),
        window_start=date(2026, 8, 27),
        window_end=date(2026, 8, 31),
        started_at=FROZEN_TIME,
    )


class RepeatingListingClient:
    def iter_listing_pages(
        self, search_target: EventbriteSearchTarget
    ) -> tuple[EventbriteListingPage, ...]:
        reference = EventbriteEventReference(
            event_id="1999042913166",
            canonical_url="https://www.eventbrite.com/e/go-hard-tickets-1999042913166",
        )
        return (
            EventbriteListingPage(
                crawl_target_id=search_target.crawl_target_id,
                url=f"https://eventbrite.test/{search_target.category}?page=1",
                search_target=search_target.label,
                page_number=1,
                http_status=200,
                duration_ms=10,
                byte_count=100,
                event_references=(reference,),
            ),
        )


class RecordingDetailFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_event_detail(self, event_id: str) -> dict[str, object]:
        self.calls.append(event_id)
        return detail(event_id)


class RateLimitedAfterOneFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_event_detail(self, event_id: str) -> dict[str, object]:
        self.calls += 1
        if self.calls == 2:
            raise EventbriteRateLimited(60)
        return detail(event_id)


def test_database_targets_group_into_independent_market_runs(database_url: str) -> None:
    new_york_market_id = uuid.uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO ingest.market (
                id, slug, name, city, region, country_code, timezone
            ) VALUES (%s, 'new-york-ny', 'New York', 'New York', 'NY', 'US',
                      'America/New_York')
            """,
            (new_york_market_id,),
        )
        connection.execute(
            """
            INSERT INTO ingest.crawl_target (
                id, source, market_id, source_location, category, enabled,
                window_days, page_cap
            ) VALUES
                (%s, 'eventbrite', %s,
                 '{"kind":"eventbrite_slug","slug":"ny--new-york"}'::jsonb,
                 'music', true, 3, 12),
                (%s, 'eventbrite', %s,
                 '{"kind":"eventbrite_slug","slug":"ny--new-york"}'::jsonb,
                 'food-and-drink', false, 4, 8)
            """,
            (
                uuid.uuid4(),
                new_york_market_id,
                uuid.uuid4(),
                new_york_market_id,
            ),
        )

    repository = IngestionRepository(database_url)
    groups = group_crawl_targets_by_market(repository.enabled_crawl_targets(source="eventbrite"))

    assert [(group.source, group.market_slug) for group in groups] == [
        ("eventbrite", "new-york-ny"),
        ("eventbrite", "philadelphia-pa"),
    ]
    assert groups[0].categories == ("music",)
    assert groups[0].maximum_window_days == 3
    assert groups[0].targets[0].page_cap == 12
    assert groups[1].categories == ("food-and-drink", "science-and-tech")


def test_source_native_location_can_use_another_platforms_shape(database_url: str) -> None:
    target_id = uuid.uuid4()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO ingest.crawl_target (
                id, source, market_id, source_location, category, enabled,
                window_days, page_cap
            ) VALUES (
                %s, 'coordinate-source',
                '8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed',
                '{"kind":"coordinates_radius","latitude":39.9526,
                  "longitude":-75.1652,"radius_km":40}'::jsonb,
                'all', true, 5, 10
            )
            """,
            (target_id,),
        )

    targets = IngestionRepository(database_url).enabled_crawl_targets(source="coordinate-source")

    assert len(targets) == 1
    assert targets[0].market_slug == "philadelphia-pa"
    assert targets[0].source_location == {
        "kind": "coordinates_radius",
        "latitude": 39.9526,
        "longitude": -75.1652,
        "radius_km": 40,
    }


def test_cross_category_id_is_staged_once_and_then_served_from_cache(
    database_url: str,
) -> None:
    repository = IngestionRepository(database_url)
    first_run = open_run(repository, "first-run")
    crawl = crawl_eventbrite_location(
        client=cast(object, RepeatingListingClient()),
        targets=(target("science-and-tech"), target("food-and-drink")),
        repository=repository,
        run_id=first_run,
        clock=frozen_clock,
    )
    fetcher = RecordingDetailFetcher()

    first_details = fetch_and_stage_eventbrite_details(
        event_references=crawl.event_references,
        fetcher=fetcher,
        repository=repository,
        run_id=first_run,
        clock=frozen_clock,
        cache_ttl=timedelta(hours=24),
    )
    second_run = open_run(repository, "second-run")
    second_details = fetch_and_stage_eventbrite_details(
        event_references=crawl.event_references,
        fetcher=fetcher,
        repository=repository,
        run_id=second_run,
        clock=frozen_clock,
        cache_ttl=timedelta(hours=24),
    )

    assert crawl.pages_fetched == 2
    assert crawl.listing_appearances == 2
    assert crawl.unique_ids == 1
    assert first_details.staged == 1
    assert first_details.fetched == 1
    assert second_details.staged == 1
    assert second_details.cached == 1
    assert fetcher.calls == ["1999042913166"]
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute("SELECT count(*) FROM source_listing").fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM ingest.page_fetch WHERE run_id = %s", (first_run,)
        ).fetchone() == (2,)
        assert connection.execute(
            """
            SELECT count(*)
            FROM ingest.page_fetch
            WHERE run_id = %s AND crawl_target_id IS NOT NULL
            """,
            (first_run,),
        ).fetchone() == (2,)


def test_429_stages_completed_details_and_marks_run_partial(database_url: str) -> None:
    repository = IngestionRepository(database_url)
    run_id = open_run(repository, "partial-run")
    references = (
        EventbriteEventReference("1001", "https://www.eventbrite.com/e/a-tickets-1001"),
        EventbriteEventReference("1002", "https://www.eventbrite.com/e/b-tickets-1002"),
    )
    summary = fetch_and_stage_eventbrite_details(
        event_references=references,
        fetcher=RateLimitedAfterOneFetcher(),
        repository=repository,
        run_id=run_id,
        clock=frozen_clock,
        cache_ttl=timedelta(hours=24),
    )
    repository.mark_partial(
        run_id=run_id,
        events_found=2,
        listing_appearances=2,
        detail_fetched=summary.fetched,
        detail_cached=summary.cached,
        events_rejected_online=0,
        reason=summary.partial_reason or "missing partial reason",
        finished_at=FROZEN_TIME,
    )

    assert summary.partial is True
    assert summary.staged == 1
    assert summary.fetched == 1
    with psycopg.connect(psycopg_url(database_url)) as connection:
        row = connection.execute(
            """
            SELECT status, listing_appearances, events_found, detail_fetched,
                   detail_cached, error_message
            FROM ingest.run WHERE id = %s
            """,
            (run_id,),
        ).fetchone()
    assert row == (
        "partial",
        2,
        2,
        1,
        0,
        "Eventbrite detail API returned 429; retry after 60 seconds",
    )
