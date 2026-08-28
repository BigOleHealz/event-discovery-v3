from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from app.seed import EVENTS, VENUES, seed_database

EXPECTED_TABLES = (
    "venue",
    "canonical_event",
    "source_listing",
    "app_user",
    "contact",
    "invite",
    "attendance",
    "saved_search",
    "push_subscription",
    "notification_log",
    "saved_search_hit",
)
EXPECTED_INGEST_TABLES = {
    "event_detail_cache",
    "run",
    "page_fetch",
    "rejected_listing",
    "geocode_cache",
}


def migration_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    return config


def table_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname = 'public'
                  AND tablename <> 'spatial_ref_sys'
                  AND tablename <> 'alembic_version'
                """
            )
        )
        return {str(row.tablename) for row in rows}


def ingest_table_names(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname = 'ingest'
                """
            )
        )
        return {str(row.tablename) for row in rows}


def test_migrations_seed_and_constraints(database_url: str) -> None:
    config = migration_config(database_url)
    engine = sa.create_engine(database_url)

    command.upgrade(config, "head")
    assert table_names(engine) == set(EXPECTED_TABLES)
    assert ingest_table_names(engine) == EXPECTED_INGEST_TABLES

    with engine.connect() as connection:
        run_columns = {
            str(row.column_name)
            for row in connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'ingest' AND table_name = 'run'
                    """
                )
            )
        }
        assert {
            "categories",
            "window_start",
            "window_end",
            "listing_appearances",
            "detail_fetched",
            "detail_cached",
        } <= run_columns
        page_columns = {
            str(row.column_name)
            for row in connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'ingest' AND table_name = 'page_fetch'
                    """
                )
            )
        }
        assert {"search_target", "page_number"} <= page_columns

    seed_database(engine)
    seed_database(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM venue")) == len(VENUES)
        assert connection.scalar(text("SELECT count(*) FROM canonical_event")) == len(EVENTS)

    event_id = EVENTS[0].id
    listing_id = uuid4()
    ingestion_run_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO source_listing (
                    id, canonical_event_id, source, source_event_id, url,
                    raw_payload, ingestion_run_id
                ) VALUES (
                    :id, :event_id, 'fixture', 'same-source-id',
                    'https://example.test/event', '{}'::jsonb, :run_id
                )
                """
            ),
            {"id": listing_id, "event_id": event_id, "run_id": ingestion_run_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO source_listing (
                    id, canonical_event_id, source, source_event_id, url,
                    raw_payload, ingestion_run_id
                ) VALUES (
                    :id, :event_id, 'fixture', 'same-source-id',
                    'https://example.test/duplicate', '{}'::jsonb, :run_id
                )
                """
            ),
            {"id": uuid4(), "event_id": event_id, "run_id": uuid4()},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("INSERT INTO app_user (id, is_shadow) VALUES (:id, false)"),
            {"id": uuid4()},
        )

    missing_run_id = uuid4()
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO ingest.page_fetch (id, run_id, url)
                VALUES (:id, :run_id, 'https://example.test/events')
                """
            ),
            {"id": uuid4(), "run_id": missing_run_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO ingest.rejected_listing (id, run_id, source, reason)
                VALUES (:id, :run_id, 'fixture', 'malformed')
                """
            ),
            {"id": uuid4(), "run_id": missing_run_id},
        )

    user_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO app_user (id, google_sub) VALUES (:id, :google_sub)"),
            {"id": user_id, "google_sub": "fixture-google-sub"},
        )
        connection.execute(
            text(
                """
                INSERT INTO notification_log (
                    id, user_id, canonical_event_id, channel, trigger
                ) VALUES (:id, :user_id, :event_id, 'push', 'nearby')
                """
            ),
            {"id": uuid4(), "user_id": user_id, "event_id": event_id},
        )

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO notification_log (
                    id, user_id, canonical_event_id, channel, trigger
                ) VALUES (:id, :user_id, :event_id, 'push', 'nearby')
                """
            ),
            {"id": uuid4(), "user_id": user_id, "event_id": event_id},
        )

    command.downgrade(config, "base")
    assert table_names(engine) == set()
    assert ingest_table_names(engine) == set()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0

    engine.dispose()
