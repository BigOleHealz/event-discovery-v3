from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from testcontainers.community.postgres import PostgresContainer

os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgis/postgis:16-3.4", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        alembic_config = Config(str(REPOSITORY_ROOT / "api" / "alembic.ini"))
        alembic_config.set_main_option(
            "script_location", str(REPOSITORY_ROOT / "api" / "migrations")
        )
        alembic_config.attributes["database_url"] = url
        command.upgrade(alembic_config, "head")
        yield url


@pytest.fixture
def clean_ingestion_tables(database_url: str) -> Iterator[None]:
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute(
            """
            TRUNCATE ingest.event_detail_cache, ingest.geocode_cache, source_listing,
                ingest.rejected_listing, ingest.page_fetch, ingest.run,
                ingest.crawl_target, venue CASCADE;
            INSERT INTO ingest.crawl_target (
                id, source, market_id, source_location, category, enabled,
                window_days, page_cap
            ) VALUES
                (
                    '4c33ed98-a96b-4d72-946f-5bd923db9506', 'eventbrite',
                    '8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed',
                    '{"kind":"eventbrite_slug","slug":"pa--philadelphia"}'::jsonb,
                    'science-and-tech', true, 5, 20
                ),
                (
                    'a8ddad4f-d5af-4f24-a8a6-99e10aa3d76f', 'eventbrite',
                    '8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed',
                    '{"kind":"eventbrite_slug","slug":"pa--philadelphia"}'::jsonb,
                    'food-and-drink', true, 5, 20
                )
            """
        )
    yield
