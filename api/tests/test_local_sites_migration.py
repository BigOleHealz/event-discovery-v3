import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError
from test_migrations import migration_config


def test_local_inventory_and_policy_round_trip(database_url: str) -> None:
    config = migration_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("""
            SELECT a.source,a.fetch_method,m.slug,a.min_request_interval_seconds,
                   a.access_policy->>'status',t.enabled,a.enabled
            FROM ingest.source_adapter a JOIN ingest.crawl_target t USING(source)
            JOIN ingest.market m ON m.id=t.market_id
            WHERE a.source IN ('charm-city-books','philamoca','reads-and-company')
            ORDER BY a.source
        """)
        ).all()
        assert rows == [
            ("charm-city-books", "stagehand", "baltimore-md", 10, "reviewed", True, True),
            ("philamoca", "http", "philadelphia-pa", 10, "reviewed", True, True),
            ("reads-and-company", "stagehand", "philadelphia-pa", 10, "reviewed", True, True),
        ]
    for assignment in (
        "min_request_interval_seconds=0",
        "access_policy=jsonb_set(access_policy,'{status}','null')",
        "access_policy=jsonb_set(access_policy,'{reviewed_at}','null')",
        "access_policy='{}'",
        "access_policy=jsonb_set(access_policy,'{status}','\"unknown\"')",
        "access_policy=jsonb_set(access_policy,'{listing_urls}','[]')",
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                sa.text(f"UPDATE ingest.source_adapter SET {assignment} WHERE source='philamoca'")
            )
    command.downgrade(config, "20260927_0013")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM ingest.source_adapter")) == 2
        assert (
            connection.scalar(
                sa.text("SELECT count(*) FROM ingest.market WHERE slug='baltimore-md'")
            )
            == 0
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM ingest.source_adapter")) == 8
    engine.dispose()
