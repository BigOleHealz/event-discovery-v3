from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError
from test_migrations import migration_config


def test_global_projection_run_exception_and_downgrade(database_url: str) -> None:
    config = migration_config(database_url)
    engine = sa.create_engine(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260922_0010")
    command.upgrade(config, "head")
    insert = sa.text("""
        INSERT INTO ingest.run (id, run_date, source, market_id, started_at,
                                status, airflow_dag_id)
        VALUES (:id, '2026-09-26', 'fixture', :market, '2026-09-26T12:00:00Z',
                'running', :dag)
    """)
    projection_id, crawl_id = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(insert, {
            "id": projection_id, "market": None, "dag": "project_to_neo4j",
        })
        connection.execute(insert, {
            "id": crawl_id, "market": "8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed",
            "dag": "ingest_eventbrite",
        })
    # Null DAG IDs must not sneak through SQL CHECK's three-valued logic.
    for dag in (None, "ingest_eventbrite", "ingest_meetup", "another_global_job"):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert, {"id": uuid.uuid4(), "market": None, "dag": dag})
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert, {
            "id": uuid.uuid4(), "market": uuid.uuid4(), "dag": "ingest_eventbrite",
        })
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(sa.text("UPDATE ingest.run SET airflow_dag_id = NULL WHERE id = :id"),
                           {"id": projection_id})
    # Downgrading never silently destroys global audit history.
    with pytest.raises(IntegrityError):
        command.downgrade(config, "20260922_0010")
    with engine.begin() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM ingest.run WHERE id = :id"),
                                 {"id": projection_id}) == 1
        connection.execute(sa.text("DELETE FROM ingest.run WHERE id = :id"), {"id": projection_id})
    command.downgrade(config, "20260922_0010")
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert, {"id": uuid.uuid4(), "market": None, "dag": "project_to_neo4j"})
    with engine.begin() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM ingest.run WHERE id = :id"),
                                 {"id": crawl_id}) == 1
        connection.execute(sa.text("DELETE FROM ingest.run WHERE id = :id"), {"id": crawl_id})
    command.upgrade(config, "head")
    engine.dispose()
