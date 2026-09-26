from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from neo4j import GraphDatabase
from neo4j.exceptions import ClientError
from testcontainers.community.neo4j import Neo4jContainer

from ingestion.graph import EDGE_QUERIES, GraphConfig, project_to_neo4j

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
pytestmark = pytest.mark.usefixtures("clean_ingestion_tables")


def uid(number: int) -> str:
    return str(uuid.UUID(int=number))


def pg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def graph_config() -> Iterator[GraphConfig]:
    with Neo4jContainer("neo4j:5.26-community", password="projection-test") as container:
        yield GraphConfig(container.get_connection_url(), "neo4j", "projection-test")


def cypher(config: GraphConfig, query: str) -> list[dict[str, object]]:
    with GraphDatabase.driver(config.uri, auth=(config.user, config.password)) as driver:
        with driver.session(database=config.database) as session:
            return session.run(query).data()


def graph_state(config: GraphConfig) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    return (
        cypher(config, """MATCH (n) RETURN labels(n) AS labels, properties(n) AS props
                          ORDER BY labels(n)[0], n.id"""),
        cypher(config, """MATCH (a)-[r]->(b)
                          RETURN a.id AS source, type(r) AS type, b.id AS target,
                                 properties(r) AS props ORDER BY type, source, target"""),
    )


def seed_projection(database_url: str) -> None:
    with psycopg.connect(pg_url(database_url)) as connection:
        for number, region, city in ((10, "PA", "Philadelphia"), (11, "PA", "Philadelphia"),
                                     (12, "NJ", "Philadelphia"), (13, None, None)):
            connection.execute(
                """INSERT INTO venue (id, name, city, region, country, location)
                   VALUES (%s, %s, %s, %s, 'US',
                           ST_SetSRID(ST_MakePoint(-75, 40), 4326)::geography)""",
                (uid(number), f"Venue {number}", city, region),
            )
        for number in (1, 2):
            connection.execute(
                """INSERT INTO canonical_event (
                       id, title, description, starts_at, ends_at, recurrence_group_id,
                       timezone, venue_id,
                       primary_category, location, created_at, updated_at, archived_at
                   ) VALUES (%s, %s, 'Description', %s, %s, %s, 'America/New_York', %s, %s,
                             ST_SetSRID(ST_MakePoint(-75, 40), 4326)::geography, %s, %s, %s)""",
                (uid(number), f"Event {number}", NOW, NOW + timedelta(hours=2),
                 uid(40) if number == 1 else None, uid(10) if number == 1 else None,
                 "Music" if number == 1 else None, NOW, NOW, NOW if number == 2 else None),
            )
        for number in (20, 21, 22):
            connection.execute(
                """INSERT INTO source_listing (
                       id, canonical_event_id, source, source_event_id, url,
                       registration_url, price_min, price_max, raw_payload,
                       ingestion_run_id, first_seen_at, last_seen_at
                   ) VALUES (%s, %s, 'fixture', %s, %s, %s, 12.50, 20.00,
                             '{"not_projected": true}', %s, %s, %s)""",
                (uid(number), uid(1) if number != 22 else None, str(number),
                 f"https://fixture.test/{number}", f"https://fixture.test/{number}/register",
                 uid(30), NOW, NOW),
            )


def assert_matches_postgres(database_url: str, config: GraphConfig) -> None:
    """Independent contract oracle: compare every node property and every edge."""
    nodes = []
    edges = []

    def node(label: str, props: dict[str, object]) -> None:
        nodes.append({"labels": [label], "props": {
            key: value for key, value in props.items() if value is not None
        }})

    def edge(source: object, kind: str, target: object) -> None:
        edges.append({"source": source, "type": kind, "target": target, "props": {}})

    with psycopg.connect(pg_url(database_url)) as connection:
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        events = connection.execute("""
            SELECT to_jsonb(e) - 'location' || jsonb_build_object(
                'latitude', ST_Y(location::geometry), 'longitude', ST_X(location::geometry))
            FROM canonical_event e ORDER BY id
        """).fetchall()
        categories = set()
        for (props,) in events:
            node("CanonicalEvent", props)
            if props["venue_id"]:
                edge(props["id"], "HELD_AT", props["venue_id"])
            if props["primary_category"] is not None:
                category = props["primary_category"]
                categories.add(category)
                edge(props["id"], "HAS_CATEGORY", category)
        for category in categories:
            node("Category", {"id": category, "name": category})
        listings = connection.execute("""
            SELECT jsonb_build_object(
                'id', id, 'canonical_event_id', canonical_event_id, 'source', source,
                'source_event_id', source_event_id, 'url', url,
                'registration_url', registration_url, 'price_min', price_min::text,
                'price_max', price_max::text, 'first_seen_at', first_seen_at,
                'last_seen_at', last_seen_at)
            FROM source_listing
        """).fetchall()
        for (props,) in listings:
            node("SourceListing", props)
            if props["canonical_event_id"]:
                edge(props["id"], "LISTS", props["canonical_event_id"])
        venues = connection.execute("""
            SELECT to_jsonb(v) - 'location' || jsonb_build_object(
                'latitude', ST_Y(location::geometry), 'longitude', ST_X(location::geometry))
            FROM venue v
        """).fetchall()
        cities = {}
        for (props,) in venues:
            if props["city"] is not None:
                city_id = json.dumps([props["city"], props["region"], props["country"]])
                props["city_id"] = city_id
                cities[city_id] = {"id": city_id, "name": props["city"],
                                   "region": props["region"], "country": props["country"]}
                edge(props["id"], "IN_CITY", city_id)
            node("Venue", props)
        for props in cities.values():
            node("City", props)
    # Compare multisets, so duplicate nodes or relationships fail too.
    actual_nodes, actual_edges = graph_state(config)
    # Compare values, not JSON number formatting (Postgres renders 40.0 as 40).
    assert sorted(actual_nodes, key=lambda row: (row["labels"], row["props"]["id"])) == sorted(
        nodes, key=lambda row: (row["labels"], row["props"]["id"]),
    )
    assert sorted(
        actual_edges, key=lambda row: (row["type"], row["source"], row["target"]),
    ) == sorted(
        edges, key=lambda row: (row["type"], row["source"], row["target"]),
    )


def test_rebuild_exact_idempotent_and_repairs_drift(
    database_url: str, graph_config: GraphConfig,
) -> None:
    seed_projection(database_url)
    first = project_to_neo4j(database_url, graph_config, airflow_run_id="same", clock=lambda: NOW)
    assert first == {"CanonicalEvent": 2, "SourceListing": 3, "Venue": 4, "City": 2,
                     "Category": 1, "LISTS": 2, "HELD_AT": 1, "HAS_CATEGORY": 1, "IN_CITY": 3}
    assert_matches_postgres(database_url, graph_config)
    before = graph_state(graph_config)
    assert project_to_neo4j(
        database_url, graph_config, airflow_run_id="same", clock=lambda: NOW,
    ) == first
    assert graph_state(graph_config) == before
    cypher(graph_config, "MATCH (n) DETACH DELETE n")
    project_to_neo4j(database_url, graph_config, airflow_run_id="recover", clock=lambda: NOW)
    assert graph_state(graph_config) == before
    cypher(graph_config, "CREATE (:Bogus {id: 'stale'})")
    with psycopg.connect(pg_url(database_url)) as connection:
        connection.execute("DELETE FROM source_listing WHERE id = %s", (uid(21),))
        connection.execute("DELETE FROM canonical_event WHERE id = %s", (uid(2),))
        connection.execute("""UPDATE canonical_event SET title = 'Changed',
                              primary_category = NULL, venue_id = NULL WHERE id = %s""", (uid(1),))
        connection.execute("DELETE FROM venue WHERE id IN (%s, %s)", (uid(10), uid(12)))
    project_to_neo4j(database_url, graph_config, airflow_run_id="update", clock=lambda: NOW)
    assert_matches_postgres(database_url, graph_config)
    with psycopg.connect(pg_url(database_url)) as connection:
        assert connection.execute("""SELECT status, count(*), min(started_at), min(finished_at)
            FROM ingest.run WHERE airflow_dag_id = 'project_to_neo4j' GROUP BY status"""
        ).fetchall() == [("success", 3, NOW, NOW)]
        connection.execute("TRUNCATE source_listing, canonical_event, venue CASCADE")
    project_to_neo4j(database_url, graph_config, airflow_run_id="empty", clock=lambda: NOW)
    assert graph_state(graph_config) == ([], [])


def test_failed_write_preserves_graph_and_retry_closes_same_run(
    database_url: str, graph_config: GraphConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_projection(database_url)
    project_to_neo4j(database_url, graph_config, airflow_run_id="initial", clock=lambda: NOW)
    before = graph_state(graph_config)
    with monkeypatch.context() as patch:
        # Fail after deletion and node recreation inside a real Neo4j transaction.
        patch.setitem(EDGE_QUERIES, "LISTS", "THIS IS INVALID CYPHER")
        with pytest.raises(ClientError):
            project_to_neo4j(database_url, graph_config, airflow_run_id="retry", clock=lambda: NOW)
    assert graph_state(graph_config) == before
    with psycopg.connect(pg_url(database_url)) as connection:
        assert connection.execute("""SELECT status, finished_at, error_message FROM ingest.run
                                   WHERE airflow_run_id = 'retry'""").fetchone() == (
            "failed", NOW, "CypherSyntaxError",
        )
    project_to_neo4j(database_url, graph_config, airflow_run_id="retry", clock=lambda: NOW)
    assert_matches_postgres(database_url, graph_config)
    with psycopg.connect(pg_url(database_url)) as connection:
        assert connection.execute("""SELECT status, error_message FROM ingest.run
                                   WHERE airflow_run_id = 'retry'""").fetchall() == [
            ("success", None),
        ]
