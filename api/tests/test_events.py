from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

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


async def request_events(application: FastAPI) -> Response:
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/api/events")


def test_events_are_valid_geojson_with_longitude_first(migrated_engine: sa.Engine) -> None:
    def override_connection() -> Iterator[Connection]:
        with migrated_engine.connect() as connection:
            yield connection

    app.dependency_overrides[get_connection] = override_connection
    app.dependency_overrides[utc_now] = lambda: datetime.fromisoformat("2026-08-27T12:00:00+00:00")
    try:
        response = anyio.run(request_events, app)
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
