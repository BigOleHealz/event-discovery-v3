from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import anyio
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
from ingestion.graph import GraphConfig, project_to_neo4j
from neo4j import GraphDatabase
from test_events import insert_spatial_event, migrated_engine  # noqa: F401

from app.categories import graph_driver
from app.clock import utc_now
from app.database import get_connection
from app.main import app

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


async def get(path: str, params: dict[str, str | int] | None = None) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, params=params)


@pytest.fixture
def category_events(
    migrated_engine: sa.Engine,  # noqa: F811
    database_url: str,
    graph_environment: GraphConfig,
) -> Iterator[None]:
    with migrated_engine.begin() as connection:
        for category in ("music", " Jazz ", "Bebop", "Rock", "Science & Technology", "New Label"):
            insert_spatial_event(
                connection,
                title=category.strip(),
                longitude=-75.16,
                latitude=39.95,
                starts_at="2030-09-27T18:00:00Z",
                primary_category=category,
            )
    project_to_neo4j(
        database_url, graph_environment, airflow_run_id="categories", clock=lambda: NOW
    )

    def connection() -> Iterator[sa.Connection]:
        with migrated_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_connection] = connection
    app.dependency_overrides[utc_now] = lambda: NOW
    yield
    app.dependency_overrides.clear()


@pytest.mark.usefixtures("category_events")
def test_cypher_descendants_drive_individual_and_aggregate_filters() -> None:
    hierarchy = anyio.run(get, "/api/categories").json()
    categories = {item["id"]: item for item in hierarchy}
    assert categories["bebop"]["parent_id"] == "jazz"
    assert categories["bebop"]["root_id"] == "music"
    assert categories["unmapped:new label"]["parent_id"] == "other"
    assert len([row for row in hierarchy if row["parent_id"] is None]) == 8
    for selected, expected in (
        ("music", {"music", "Jazz", "Bebop", "Rock"}),
        ("Jazz", {"Jazz", "Bebop"}),
        ("bebop", {"Bebop"}),
        ("jazz,rock", {"Jazz", "Bebop", "Rock"}),
        ("music,jazz", {"music", "Jazz", "Bebop", "Rock"}),
        ("science-and-tech", {"Science & Technology"}),
        ("other", {"New Label"}),
        ("missing", set()),
    ):
        for zoom in (12, 14):
            response = anyio.run(
                get,
                "/api/events",
                {
                    "categories": selected,
                    "zoom": zoom,
                    "starts_after": "2030-09-01T00:00:00Z",
                    "starts_before": "2030-10-01T00:00:00Z",
                },
            )
            assert response.status_code == 200, response.text
            features = response.json()["features"]
            if zoom == 14:
                assert {item["properties"]["title"] for item in features} == expected
                assert len(features) == len(expected)
            else:
                assert sum(item["properties"]["count"] for item in features) == len(expected)


@pytest.mark.usefixtures("category_events")
def test_filtering_really_uses_cypher_edges(graph_environment: GraphConfig) -> None:
    with GraphDatabase.driver(
        graph_environment.uri, auth=("neo4j", graph_environment.password)
    ) as d:
        with d.session() as session:
            session.run("MATCH (:Category {id: 'jazz'})-[r:SUBCATEGORY_OF]->() DELETE r").consume()
    response = anyio.run(
        get,
        "/api/events",
        {
            "categories": "music",
            "zoom": 14,
            "starts_after": "2030-09-01T00:00:00Z",
        },
    )
    assert {item["properties"]["title"] for item in response.json()["features"]} == {
        "music",
        "Rock",
    }


@pytest.mark.usefixtures("category_events")
def test_unavailable_graph_returns_503_for_category_queries_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_driver().close()
    graph_driver.cache_clear()
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    try:
        assert anyio.run(get, "/api/categories").status_code == 503
        assert anyio.run(get, "/api/events", {"categories": "music"}).status_code == 503
        assert anyio.run(get, "/api/events").status_code == 200
    finally:
        graph_driver().close()
        graph_driver.cache_clear()
