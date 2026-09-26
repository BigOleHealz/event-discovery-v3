"""Postgres-owned, fully replaceable graph projection.

The configured Neo4j database is dedicated to this projection. A rebuild reads
one repeatable-read snapshot, then replaces all nodes/edges in one transaction.
No raw payloads, embeddings, credentials, or ingestion metadata enter the graph.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import psycopg
from neo4j import GraphDatabase, ManagedTransaction
from psycopg.rows import dict_row

from ingestion.clock import Clock
from ingestion.database import RUN_NAMESPACE

Property = str | int | float | bool | None
Properties = dict[str, Property]

# SQL and Cypher identifiers below are constants, never user input.
NODE_QUERIES = {
    "CanonicalEvent": """
        SELECT id, title, description, starts_at, ends_at, timezone, venue_id,
               primary_category, recurrence_group_id, created_at, updated_at, archived_at,
               ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude
        FROM canonical_event ORDER BY id
    """,
    "SourceListing": """
        SELECT id, canonical_event_id, source, source_event_id, url, registration_url,
               price_min, price_max, first_seen_at, last_seen_at
        FROM source_listing ORDER BY id
    """,
    "Venue": """
        SELECT id, name, formatted_address, google_place_id, city, region, country,
               ST_Y(location::geometry) AS latitude, ST_X(location::geometry) AS longitude,
               CASE WHEN city IS NOT NULL
                    THEN jsonb_build_array(city, region, country)::text END AS city_id
        FROM venue ORDER BY id
    """,
    "City": """
        SELECT DISTINCT jsonb_build_array(city, region, country)::text AS id,
               city AS name, region, country
        FROM venue WHERE city IS NOT NULL ORDER BY id
    """,
    "Category": """
        SELECT DISTINCT primary_category AS id, primary_category AS name
        FROM canonical_event WHERE primary_category IS NOT NULL ORDER BY id
    """,
}

EDGE_QUERIES = {
    "LISTS": """
        MATCH (a:SourceListing), (b:CanonicalEvent)
        WHERE a.canonical_event_id = b.id CREATE (a)-[:LISTS]->(b)
    """,
    "HAS_CATEGORY": """
        MATCH (a:CanonicalEvent), (b:Category)
        WHERE a.primary_category = b.id CREATE (a)-[:HAS_CATEGORY]->(b)
    """,
    "HELD_AT": """
        MATCH (a:CanonicalEvent), (b:Venue)
        WHERE a.venue_id = b.id CREATE (a)-[:HELD_AT]->(b)
    """,
    "IN_CITY": """
        MATCH (a:Venue), (b:City)
        WHERE a.city_id = b.id CREATE (a)-[:IN_CITY]->(b)
    """,
}


@dataclass(frozen=True)
class GraphConfig:
    uri: str
    user: str
    password: str = field(repr=False)
    database: str = "neo4j"

    @classmethod
    def from_env(cls) -> GraphConfig:
        values = {}
        for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
            value = os.environ.get(key, "")
            if not value.strip():
                raise ValueError(f"{key} is required")
            values[key] = value
        return cls(
            values["NEO4J_URI"], values["NEO4J_USER"],
            values["NEO4J_PASSWORD"], values["NEO4J_DATABASE"],
        )


def _property(value: object) -> Property:
    # Decimal strings preserve exact prices; ISO strings preserve timezone/precision.
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported graph property: {type(value).__name__}")


def _snapshot(connection: psycopg.Connection[tuple[object, ...]]) -> dict[str, list[Properties]]:
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        with connection.cursor(row_factory=dict_row) as cursor:
            return {
                label: [
                    {key: _property(value) for key, value in row.items()}
                    for row in cursor.execute(query).fetchall()
                ]
                for label, query in NODE_QUERIES.items()
            }


def _replace_graph(
    transaction: ManagedTransaction, snapshot: dict[str, list[Properties]]
) -> dict[str, int]:
    transaction.run("MATCH (n) DETACH DELETE n").consume()
    counts = {}
    for label, rows in snapshot.items():
        for offset in range(0, len(rows), 500):
            transaction.run(
                f"UNWIND $rows AS row CREATE (n:{label}) SET n = row",
                rows=rows[offset:offset + 500],
            ).consume()
        counts[label] = len(rows)
    for relationship, query in EDGE_QUERIES.items():
        counts[relationship] = transaction.run(query).consume().counters.relationships_created
    return counts


def project_to_neo4j(
    database_url: str, config: GraphConfig, *, airflow_run_id: str, clock: Clock
) -> dict[str, int]:
    """Rebuild from a consistent snapshot, serializing even manual invocations.

    Retry the whole rebuild on failure. A deterministic ingest.run id records the
    latest attempt for one Airflow run. Graph commits and run bookkeeping cannot
    be atomic across databases; replay repairs a crash between those two commits.
    """
    run_id = uuid.uuid5(RUN_NAMESPACE, f"project_to_neo4j:{airflow_run_id}")
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as connection:
        # Session lock precedes the snapshot; waiting writers never publish older data.
        # Closing the connection releases the lock, including on exceptions.
        connection.execute("SELECT pg_advisory_lock(hashtextextended('project_to_neo4j', 0))")
        started_at = clock()
        connection.execute(
            """INSERT INTO ingest.run (
                   id, run_date, source, started_at, status, airflow_dag_id, airflow_run_id
               ) VALUES (%s, %s, 'postgres', %s, 'running', 'project_to_neo4j', %s)
               ON CONFLICT (id) DO UPDATE SET
                   run_date = EXCLUDED.run_date, started_at = EXCLUDED.started_at,
                   finished_at = NULL, status = 'running', error_message = NULL,
                   events_found = 0""",
            (run_id, started_at.date(), started_at, airflow_run_id),
        )
        try:
            snapshot = _snapshot(connection)
            with GraphDatabase.driver(config.uri, auth=(config.user, config.password)) as driver:
                with driver.session(database=config.database) as session:
                    for label in NODE_QUERIES:
                        session.run(
                            f"CREATE CONSTRAINT projection_{label}_id IF NOT EXISTS "
                            f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                        ).consume()
                    counts = session.execute_write(_replace_graph, snapshot)
            connection.execute(
                """UPDATE ingest.run SET status = 'success', finished_at = %s,
                       events_found = %s WHERE id = %s""",
                (clock(), counts["CanonicalEvent"], run_id),
            )
            return counts
        except Exception as error:
            # Driver exceptions can include connection details; keep credentials out of metadata.
            connection.execute(
                """UPDATE ingest.run SET status = 'failed', finished_at = %s,
                       error_message = %s WHERE id = %s""",
                (clock(), type(error).__name__, run_id),
            )
            raise
