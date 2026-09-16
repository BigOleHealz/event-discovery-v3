from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import DataError
from test_migrations import migration_config


def test_pgvector_upgrade_downgrade_and_existing_rows(database_url: str) -> None:
    config = migration_config(database_url)
    engine = sa.create_engine(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260828_0007")
    with engine.begin() as connection:
        connection.execute(
            sa.text("""
            INSERT INTO canonical_event (id, title, starts_at, timezone, location)
            VALUES ('00000000-0000-0000-0000-000000000001', 'Weekly occurrence', now(),
                'America/New_York', ST_SetSRID(ST_MakePoint(-75, 40), 4326)::geography);
            INSERT INTO source_listing (
                id, source, source_event_id, url, raw_payload, ingestion_run_id
            )
            VALUES ('00000000-0000-0000-0000-000000000002', 'fixture', 'vector-migration',
                'https://example.test', '{}', '00000000-0000-0000-0000-000000000003');
        """)
        )
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert (
            connection.scalar(
                sa.text("""
            SELECT format_type(atttypid, atttypmod) FROM pg_attribute
            WHERE attrelid = 'source_listing'::regclass AND attname = 'embedding'
        """)
            )
            == "vector(1536)"
        )
        index = connection.scalar(
            sa.text("""
            SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_source_listing_embedding'
        """)
        )
        assert "USING hnsw (embedding vector_cosine_ops)" in index
        assert " WHERE " not in index
        assert connection.execute(
            sa.text("""
            SELECT embedding FROM source_listing WHERE source_event_id = 'vector-migration'
        """)
        ).one() == (None,)
        assert connection.execute(
            sa.text("""
            SELECT recurrence_group_id FROM canonical_event
            WHERE id = '00000000-0000-0000-0000-000000000001'
        """)
        ).one() == (None,)
        connection.execute(
            sa.text("""
            UPDATE canonical_event SET recurrence_group_id =
                '00000000-0000-0000-0000-000000000004'
            WHERE id = '00000000-0000-0000-0000-000000000001'
        """)
        )
    with pytest.raises(DataError), engine.begin() as connection:
        connection.execute(
            sa.text("""
            UPDATE source_listing SET embedding = '[1,2,3]'::vector
            WHERE source_event_id = 'vector-migration'
        """)
        )
    command.downgrade(config, "20260828_0007")
    with engine.begin() as connection:
        assert (
            connection.scalar(
                sa.text("""
            SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public'
            AND ((table_name = 'source_listing' AND column_name = 'embedding')
            OR (table_name = 'canonical_event' AND column_name = 'recurrence_group_id'))
        """)
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.text("""
            SELECT count(*) FROM source_listing WHERE source_event_id = 'vector-migration'
        """)
            )
            == 1
        )
        connection.execute(
            sa.text("""
            DELETE FROM source_listing WHERE source_event_id = 'vector-migration';
            DELETE FROM canonical_event WHERE id = '00000000-0000-0000-0000-000000000001';
        """)
        )
    command.upgrade(config, "head")
    engine.dispose()
