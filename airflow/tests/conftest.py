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
                ingest.rejected_listing, ingest.page_fetch, ingest.run, venue CASCADE
            """
        )
    yield
