from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import anyio
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
from ingestion.graph import GraphConfig, project_to_neo4j
from neo4j import GraphDatabase
from test_events import migrated_engine  # noqa: F401

from app.clock import utc_now
from app.database import get_connection
from app.graph import graph_driver
from app.main import app

NOW = datetime(2050, 9, 27, 12, tzinfo=UTC)


def uid(number: int) -> str:
    return str(UUID(int=number))


async def get(event: int | str) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        identifier = uid(event) if isinstance(event, int) else event
        return await client.get(f"/api/events/{identifier}/similar")


@pytest.fixture
def similar_data(
    migrated_engine: sa.Engine,  # noqa: F811
    database_url: str,
    graph_environment: GraphConfig,
) -> Iterator[sa.Engine]:
    with migrated_engine.begin() as connection:
        # Higher-scored 101..103 become ineligible after projection. Remaining
        # scores exercise ranking, ties, and filtering before LIMIT.
        for number in range(100, 112):
            connection.execute(
                sa.text("""INSERT INTO canonical_event
                (id, title, starts_at, timezone, primary_category, location)
                VALUES (:id, :title, :starts, 'America/New_York', 'Jazz',
                        ST_SetSRID(ST_MakePoint(-75.16, 39.95),4326)::geography)"""),
                {"id": uid(number), "title": f"Event {number}", "starts": NOW + timedelta(hours=1)},
            )
            connection.execute(
                sa.text("""INSERT INTO source_listing
                (id, canonical_event_id, source, source_event_id, url,
                 raw_payload, ingestion_run_id)
                VALUES (:id, :event, 'fixture', :source_id, :url, '{}', :run)"""),
                {
                    "id": uid(number + 100),
                    "event": uid(number),
                    "source_id": str(number),
                    "url": f"https://fixture.test/{number}",
                    "run": uid(999),
                },
            )
        for number in range(101, 112):
            connection.execute(
                sa.text("""UPDATE source_listing SET dedup_match_id = :match,
                dedup_similarity = :score WHERE id = :id"""),
                {
                    "match": uid(200),
                    "id": uid(number + 100),
                    "score": {101: 0.95, 102: 0.95, 103: 0.95, 104: 0.85, 110: 0.86, 111: -0.2}.get(
                        number, 0.8
                    ),
                },
            )
    project_to_neo4j(database_url, graph_environment, airflow_run_id="similar", clock=lambda: NOW)
    with migrated_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE canonical_event SET archived_at = :now WHERE id = :id"),
            {"now": NOW, "id": uid(101)},
        )
        connection.execute(
            sa.text("UPDATE canonical_event SET starts_at = :past WHERE id = :id"),
            {"past": NOW - timedelta(days=1), "id": uid(102)},
        )
        connection.execute(
            sa.text("DELETE FROM source_listing WHERE canonical_event_id = :id"), {"id": uid(103)}
        )
        connection.execute(sa.text("DELETE FROM canonical_event WHERE id = :id"), {"id": uid(103)})
        connection.execute(
            sa.text("UPDATE canonical_event SET title = 'Fresh title' WHERE id = :id"),
            {"id": uid(104)},
        )

    def connection() -> Iterator[sa.Connection]:
        with migrated_engine.connect() as conn:
            yield conn

    app.dependency_overrides[get_connection] = connection
    app.dependency_overrides[utc_now] = lambda: NOW
    try:
        yield migrated_engine
    finally:
        app.dependency_overrides.clear()


@pytest.mark.usefixtures("similar_data")
def test_ranked_neighbours_use_current_postgres_details_and_filter_before_limit() -> None:
    response = anyio.run(get, 100)
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["event"]["id"] for item in items] == [uid(i) for i in (110, 104, 105, 106, 107)]
    assert [item["score"] for item in items] == [0.86, 0.85, 0.8, 0.8, 0.8]
    assert items[1]["event"]["properties"]["title"] == "Fresh title"
    assert items[1]["event"]["properties"]["registration_links"] == [
        {"source": "fixture", "url": "https://fixture.test/104"},
    ]
    reverse = anyio.run(get, 104).json()
    assert [item["event"]["id"] for item in reverse] == [uid(100)]
    assert anyio.run(get, 111).json() == []
    assert anyio.run(get, 999).status_code == 404
    assert anyio.run(get, 101).status_code == 404
    assert anyio.run(get, "bad-id").status_code == 422


@pytest.mark.usefixtures("similar_data")
def test_similar_events_follow_cypher_edges(graph_environment: GraphConfig) -> None:
    with GraphDatabase.driver(
        graph_environment.uri, auth=(graph_environment.user, graph_environment.password)
    ) as driver:
        with driver.session() as session:
            session.run("MATCH ()-[r:SIMILAR_TO]-() DELETE r").consume()
    assert anyio.run(get, 100).json() == []


@pytest.mark.usefixtures("similar_data")
def test_similar_events_graph_outage_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    graph_driver().close()
    graph_driver.cache_clear()
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    try:
        assert anyio.run(get, 100).status_code == 503
    finally:
        graph_driver().close()
        graph_driver.cache_clear()
