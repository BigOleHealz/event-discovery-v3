from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from uuid import uuid4

import anyio
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import Connection

from app.clock import utc_now
from app.database import get_connection
from app.events import AGGREGATED_BOUNDED_EVENT_QUERY, BOUNDED_EVENT_QUERY, grid_cell_size
from app.main import app
from app.seed import EVENTS, seed_database


@pytest.fixture
def migrated_engine(database_url: str) -> Iterator[sa.Engine]:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    seed_database(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


async def request_events(
    application: FastAPI,
    params: dict[str, float | int] | None = None,
) -> Response:
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/api/events", params=params)


def insert_spatial_event(
    connection: Connection,
    *,
    title: str,
    longitude: float,
    latitude: float,
) -> None:
    connection.execute(
        sa.text(
            """
            INSERT INTO canonical_event (
                id, title, starts_at, timezone, location, primary_category
            ) VALUES (
                :id, :title, '2026-09-01T12:00:00Z', 'America/New_York',
                ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                'community'
            )
            """
        ),
        {
            "id": uuid4(),
            "title": title,
            "longitude": longitude,
            "latitude": latitude,
        },
    )


def nested_index_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        index_name = value.get("Index Name")
        if isinstance(index_name, str):
            names.add(index_name)
        for child in value.values():
            names.update(nested_index_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(nested_index_names(child))
    return names


def test_events_are_valid_geojson_with_longitude_first(migrated_engine: sa.Engine) -> None:
    registration_url = "https://www.eventbrite.com/e/parkway-jazz-night"
    with migrated_engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO source_listing (
                    id, canonical_event_id, source, source_event_id, url,
                    registration_url, raw_payload, ingestion_run_id
                ) VALUES (
                    :id, :canonical_event_id, 'eventbrite', :source_event_id,
                    :url, :registration_url, '{}'::jsonb, :ingestion_run_id
                )
                """
            ),
            {
                "id": uuid4(),
                "canonical_event_id": EVENTS[0].id,
                "source_event_id": "parkway-jazz-night-test",
                "url": registration_url,
                "registration_url": registration_url,
                "ingestion_run_id": uuid4(),
            },
        )

    def override_connection() -> Iterator[Connection]:
        with migrated_engine.connect() as connection:
            yield connection

    app.dependency_overrides[get_connection] = override_connection
    app.dependency_overrides[utc_now] = lambda: datetime.fromisoformat("2026-08-27T12:00:00+00:00")
    try:
        response = anyio.run(request_events, app, {"zoom": 13})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == len(EVENTS)
    assert all(feature["type"] == "Feature" for feature in payload["features"])
    assert all(feature["geometry"]["type"] == "Point" for feature in payload["features"])

    jazz_night = next(
        feature
        for feature in payload["features"]
        if feature["properties"]["title"] == "Parkway Jazz Night"
    )
    assert jazz_night["geometry"]["coordinates"] == pytest.approx([-75.1809, 39.9656])
    assert jazz_night["properties"]["venue"]["name"] == "Philadelphia Museum of Art"
    assert jazz_night["properties"]["registration_links"] == [
        {"source": "eventbrite", "url": registration_url}
    ]

    science = next(
        feature
        for feature in payload["features"]
        if feature["properties"]["title"] == "Science After Hours: City Lights"
    )
    assert science["properties"]["registration_links"] == []


def test_bounding_box_includes_events_on_both_sides_of_a_state_line(
    migrated_engine: sa.Engine,
) -> None:
    with migrated_engine.begin() as connection:
        insert_spatial_event(
            connection,
            title="Pennsylvania River Event",
            longitude=-75.13,
            latitude=39.95,
        )
        insert_spatial_event(
            connection,
            title="New Jersey River Event",
            longitude=-75.02,
            latitude=39.95,
        )
        insert_spatial_event(
            connection,
            title="Outside Viewport Event",
            longitude=-74.60,
            latitude=39.95,
        )

    def override_connection() -> Iterator[Connection]:
        with migrated_engine.connect() as connection:
            yield connection

    app.dependency_overrides[get_connection] = override_connection
    app.dependency_overrides[utc_now] = lambda: datetime.fromisoformat("2026-08-27T12:00:00+00:00")
    try:
        response = anyio.run(
            request_events,
            app,
            {"north": 40.10, "south": 39.80, "east": -74.90, "west": -75.25},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    titles = {feature["properties"]["title"] for feature in response.json()["features"]}
    assert "Pennsylvania River Event" in titles
    assert "New Jersey River Event" in titles
    assert "Outside Viewport Event" not in titles


def test_bounding_box_supports_an_antimeridian_crossing(
    migrated_engine: sa.Engine,
) -> None:
    with migrated_engine.begin() as connection:
        insert_spatial_event(
            connection,
            title="West of Date Line",
            longitude=179.0,
            latitude=0.0,
        )
        insert_spatial_event(
            connection,
            title="East of Date Line",
            longitude=-179.0,
            latitude=0.0,
        )
        insert_spatial_event(
            connection,
            title="Greenwich Event",
            longitude=0.0,
            latitude=0.0,
        )

    def override_connection() -> Iterator[Connection]:
        with migrated_engine.connect() as connection:
            yield connection

    app.dependency_overrides[get_connection] = override_connection
    app.dependency_overrides[utc_now] = lambda: datetime.fromisoformat("2026-08-27T12:00:00+00:00")
    try:
        response = anyio.run(
            request_events,
            app,
            {"north": 10.0, "south": -10.0, "east": -170.0, "west": 170.0},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    titles = {feature["properties"]["title"] for feature in response.json()["features"]}
    assert {"West of Date Line", "East of Date Line"} <= titles
    assert "Greenwich Event" not in titles


def test_bounding_box_requires_all_coordinates(migrated_engine: sa.Engine) -> None:
    def override_connection() -> Iterator[Connection]:
        with migrated_engine.connect() as connection:
            yield connection

    app.dependency_overrides[get_connection] = override_connection
    try:
        response = anyio.run(request_events, app, {"north": 40.0, "south": 39.0})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"] == "north, south, east, and west must be supplied together"


def test_grid_aggregation_counts_sum_to_the_underlying_events(
    migrated_engine: sa.Engine,
) -> None:
    bounds = {"north": 40.10, "south": 39.80, "east": -74.90, "west": -75.30}
    current_time = datetime.fromisoformat("2026-08-27T12:00:00+00:00")
    with migrated_engine.begin() as connection:
        for fixture_number in range(3):
            insert_spatial_event(
                connection,
                title=f"Grid fixture {fixture_number}",
                longitude=-75.1652,
                latitude=39.9526,
            )
        underlying_event_count = connection.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM canonical_event AS event
                WHERE event.archived_at IS NULL
                  AND event.starts_at >= :current_time
                  AND event.location && ST_MakeEnvelope(
                      :west, :south, :east, :north, 4326
                  )::geography
                """
            ),
            {"current_time": current_time, **bounds},
        ).scalar_one()

    def override_connection() -> Iterator[Connection]:
        with migrated_engine.connect() as connection:
            yield connection

    app.dependency_overrides[get_connection] = override_connection
    app.dependency_overrides[utc_now] = lambda: current_time
    try:
        response = anyio.run(request_events, app, {**bounds, "zoom": 12})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    cells = response.json()["features"]
    assert sum(cell["properties"]["count"] for cell in cells) == underlying_event_count
    assert any(cell["properties"]["count"] >= 3 for cell in cells)
    assert all(len(cell["properties"]["top_categories"]) <= 3 for cell in cells)


def test_bounding_query_plan_uses_the_postgis_gist_index(
    migrated_engine: sa.Engine,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO canonical_event (
                    id, title, starts_at, timezone, location, primary_category
                )
                SELECT
                    gen_random_uuid(),
                    'Query plan fixture ' || fixture_number,
                    '2030-01-01T12:00:00Z',
                    'UTC',
                    ST_SetSRID(
                        ST_MakePoint(
                            -120 + (fixture_number % 1000) * 0.001,
                            35 + (fixture_number % 500) * 0.001
                        ),
                        4326
                    )::geography,
                    'fixture'
                FROM generate_series(1, 5000) AS fixture_number
                """
            )
        )
        connection.execute(sa.text("ANALYZE canonical_event"))
        plan = connection.execute(
            sa.text(f"EXPLAIN (FORMAT JSON) {BOUNDED_EVENT_QUERY.text}"),
            {
                "current_time": datetime.fromisoformat("2026-08-27T12:00:00+00:00"),
                "north": 40.10,
                "south": 39.80,
                "east": -74.90,
                "west": -75.25,
            },
        ).scalar_one()

    assert "ix_canonical_event_location" in nested_index_names(plan)


def test_grid_aggregation_query_plan_uses_the_postgis_gist_index(
    migrated_engine: sa.Engine,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO canonical_event (
                    id, title, starts_at, timezone, location, primary_category
                )
                SELECT
                    gen_random_uuid(),
                    'Grid plan fixture ' || fixture_number,
                    '2030-01-01T12:00:00Z',
                    'UTC',
                    ST_SetSRID(
                        ST_MakePoint(
                            -120 + (fixture_number % 1000) * 0.001,
                            35 + (fixture_number % 500) * 0.001
                        ),
                        4326
                    )::geography,
                    'fixture'
                FROM generate_series(1, 5000) AS fixture_number
                """
            )
        )
        connection.execute(sa.text("ANALYZE canonical_event"))
        plan = connection.execute(
            sa.text(f"EXPLAIN (FORMAT JSON) {AGGREGATED_BOUNDED_EVENT_QUERY.text}"),
            {
                "current_time": datetime.fromisoformat("2026-08-27T12:00:00+00:00"),
                "north": 40.10,
                "south": 39.80,
                "east": -74.90,
                "west": -75.25,
                "cell_size": grid_cell_size(12),
            },
        ).scalar_one()

    assert "ix_canonical_event_location" in nested_index_names(plan)
