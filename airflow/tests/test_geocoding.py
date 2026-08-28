from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
import psycopg
import pytest

from ingestion.database import IngestionRepository
from ingestion.geocode_pipeline import geocode_pending
from ingestion.geocode_repository import GeocodeRepository
from ingestion.geocoding import (
    GEOCODING_FIELD_MASK,
    GeocodingNotFound,
    GoogleGeocoder,
    GoogleGeocodingConfig,
    parse_geocoding_response,
)
from ingestion.models import FetchedPage
from ingestion.pipeline import fetch_and_stage

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GOOGLE_FIXTURE_PATH = REPOSITORY_ROOT / "tests" / "fixtures" / "google" / ("geocode_city_hall.json")
EVENTBRITE_FIXTURE_PATH = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "eventbrite" / ("organization_events_page_1.json")
)
FROZEN_TIME = datetime(2026, 8, 27, 19, 0, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("clean_ingestion_tables")


def frozen_clock() -> datetime:
    return FROZEN_TIME


def psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def google_config() -> GoogleGeocodingConfig:
    return GoogleGeocodingConfig(
        api_key="recorded-google-test-key",
        api_base_url="https://geocode.test/v4/geocode/address",
        request_timeout_seconds=5,
        region_code="US",
        language_code="en",
    )


def test_config_uses_default_timeout_for_a_blank_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_GEOCODING_API_KEY", "recorded-google-test-key")
    monkeypatch.setenv(
        "GOOGLE_GEOCODING_API_BASE_URL", "https://geocode.test/v4/geocode/address"
    )
    monkeypatch.setenv("GOOGLE_GEOCODING_TIMEOUT_SECONDS", "")

    parsed = GoogleGeocodingConfig.from_env()

    assert parsed.request_timeout_seconds == 10


def eventbrite_event() -> dict[str, object]:
    value = json.loads(EVENTBRITE_FIXTURE_PATH.read_text())
    if not isinstance(value, dict):
        raise TypeError("recorded Eventbrite page must be an object")
    events = value.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        raise TypeError("recorded Eventbrite page must contain an event")
    return cast(dict[str, object], events[0])


def stage_event(database_url: str) -> None:
    repository = IngestionRepository(database_url)
    run_id = repository.open_run(
        dag_id="ingest_eventbrite",
        airflow_run_id="geocoding-test-run",
        source_url="https://eventbrite.test/v3/organizations/recorded/events/",
        city="Philadelphia",
        started_at=FROZEN_TIME,
    )
    fetch_and_stage(
        pages=(
            FetchedPage(
                url="https://eventbrite.test/v3/organizations/recorded/events/?page=1",
                http_status=200,
                duration_ms=17,
                byte_count=2048,
                events=(eventbrite_event(),),
            ),
        ),
        repository=repository,
        run_id=run_id,
        clock=frozen_clock,
    )


def scalar(database_url: str, query: str) -> object:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        row = connection.execute(query).fetchone()
    if row is None:
        raise AssertionError("query returned no row")
    return row[0]


def test_parser_extracts_recorded_google_geocode() -> None:
    result = parse_geocoding_response(GOOGLE_FIXTURE_PATH.read_bytes())

    assert result.google_place_id == "ChIJG339Wi7GxokR120qaddoz30"
    assert result.formatted_address == ("1400 John F Kennedy Blvd, Philadelphia, PA 19107, USA")
    assert result.latitude == 39.9523882
    assert result.longitude == -75.1640233
    assert result.city == "Philadelphia"
    assert result.region == "PA"
    assert result.country == "US"


def test_recorded_geocoder_and_database_cache_prevent_second_api_call(
    database_url: str,
) -> None:
    stage_event(database_url)
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert request.headers["X-Goog-Api-Key"] == "recorded-google-test-key"
        assert request.headers["X-Goog-FieldMask"] == GEOCODING_FIELD_MASK
        assert request.url.params["regionCode"] == "US"
        assert request.url.params["languageCode"] == "en"
        return httpx.Response(
            200,
            content=GOOGLE_FIXTURE_PATH.read_bytes(),
            request=request,
        )

    repository = GeocodeRepository(database_url)
    ingestion_repository = IngestionRepository(database_url)
    with GoogleGeocoder(google_config(), transport=httpx.MockTransport(handler)) as geocoder:
        first = geocode_pending(
            repository=repository,
            ingestion_repository=ingestion_repository,
            geocoder=geocoder,
            clock=frozen_clock,
        )
        second = geocode_pending(
            repository=repository,
            ingestion_repository=ingestion_repository,
            geocoder=geocoder,
            clock=frozen_clock,
        )

    assert request_count == 1
    assert first.api_calls == 1
    assert first.cache_hits == 0
    assert second.api_calls == 0
    assert second.cache_hits == 1
    assert scalar(database_url, "SELECT count(*) FROM venue") == 1
    assert scalar(database_url, "SELECT count(*) FROM ingest.geocode_cache") == 1
    assert scalar(database_url, "SELECT google_place_id FROM venue") == (
        "ChIJG339Wi7GxokR120qaddoz30"
    )
    with psycopg.connect(psycopg_url(database_url)) as connection:
        coordinates = connection.execute(
            "SELECT ST_X(location::geometry), ST_Y(location::geometry) FROM venue"
        ).fetchone()
    assert coordinates == pytest.approx((-75.1640233, 39.9523882))


def test_empty_geocode_response_rejects_listing_and_avoids_future_calls(
    database_url: str,
) -> None:
    stage_event(database_url)
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"results": []}, request=request)

    repository = GeocodeRepository(database_url)
    ingestion_repository = IngestionRepository(database_url)
    with GoogleGeocoder(google_config(), transport=httpx.MockTransport(handler)) as geocoder:
        first = geocode_pending(
            repository=repository,
            ingestion_repository=ingestion_repository,
            geocoder=geocoder,
            clock=frozen_clock,
        )
        second = geocode_pending(
            repository=repository,
            ingestion_repository=ingestion_repository,
            geocoder=geocoder,
            clock=frozen_clock,
        )

    assert request_count == 1
    assert first.rejected_no_location == 1
    assert second.pending_listings == 0
    assert scalar(database_url, "SELECT count(*) FROM source_listing") == 0
    assert (
        scalar(
            database_url,
            "SELECT count(*) FROM ingest.rejected_listing WHERE reason = 'no_location'",
        )
        == 1
    )


def test_parser_raises_typed_not_found_for_empty_results() -> None:
    with pytest.raises(GeocodingNotFound):
        parse_geocoding_response(b'{"results": []}')
