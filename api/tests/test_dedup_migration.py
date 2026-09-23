from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError
from test_migrations import migration_config


def test_dedup_upgrade_preserves_exact_shortcut_and_reverses(database_url: str) -> None:
    config = migration_config(database_url)
    engine = sa.create_engine(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260916_0008")
    event_id = uuid.uuid4()
    representative, exact, staged = (uuid.uuid4() for _ in range(3))
    with engine.begin() as connection:
        connection.execute(sa.text("""
            INSERT INTO canonical_event (id, title, starts_at, timezone, location)
            VALUES (:id, 'One occurrence', '2026-10-06T23:30:00Z', 'America/New_York',
                ST_SetSRID(ST_MakePoint(-75.185, 39.9522), 4326)::geography)
        """), {"id": event_id})
        for listing_id in (representative, exact, staged):
            connection.execute(sa.text("""
                INSERT INTO source_listing (
                    id, canonical_event_id, source, source_event_id, url,
                    raw_payload, ingestion_run_id, embedding
                ) VALUES (:id, :event, 'fixture', :source_id, 'https://fixture.test',
                          '{}', :run, CAST(:embedding AS vector))
            """), {
                "id": listing_id,
                "event": None if listing_id == staged else event_id,
                "source_id": str(listing_id),
                "run": uuid.uuid4(),
                "embedding": json.dumps([1] + [0] * 1535)
                if listing_id == representative else None,
            })
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        states = dict(connection.execute(sa.text("""
            SELECT id, dedup_state FROM source_listing WHERE id IN (:a, :b, :c)
        """), {"a": representative, "b": exact, "c": staged}).all())
        assert states == {representative: "pending", exact: "exact", staged: "pending"}
        assert connection.scalar(sa.text("""
            SELECT embedding IS NOT NULL FROM source_listing WHERE id = :id
        """), {"id": representative})
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(sa.text("""
            UPDATE source_listing SET dedup_state = 'typo' WHERE id = :id
        """), {"id": staged})
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(sa.text("""
            UPDATE source_listing SET dedup_match_id = :missing WHERE id = :id
        """), {"missing": uuid.uuid4(), "id": staged})
    with engine.begin() as connection:
        connection.execute(sa.text("""
            UPDATE source_listing SET dedup_state = 'review', dedup_match_id = :match,
                dedup_similarity = 0.8, dedup_checked_at = '2026-09-16T12:00:00Z'
            WHERE id = :id
        """), {"match": representative, "id": staged})
    command.downgrade(config, "20260916_0008")
    with engine.begin() as connection:
        assert connection.scalar(sa.text("""
            SELECT count(*) FROM information_schema.columns
            WHERE table_name = 'source_listing' AND column_name LIKE 'dedup_%'
        """)) == 0
        assert connection.scalar(sa.text("""
            SELECT count(*) FROM source_listing WHERE id IN (:a, :b, :c)
        """), {"a": representative, "b": exact, "c": staged}) == 3
        connection.execute(sa.text("""
            DELETE FROM source_listing WHERE id IN (:a, :b, :c)
        """), {"a": representative, "b": exact, "c": staged})
        connection.execute(sa.text("DELETE FROM canonical_event WHERE id = :id"), {"id": event_id})
    command.upgrade(config, "head")
    engine.dispose()
