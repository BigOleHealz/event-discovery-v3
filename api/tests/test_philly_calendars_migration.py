import sqlalchemy as sa
from alembic import command
from test_migrations import migration_config


def test_philly_calendar_inventory_round_trip(database_url: str) -> None:
    config = migration_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text("""
            SELECT a.source,a.fetch_method,a.extraction->>'format',a.enabled,
                   a.access_policy->>'status',a.min_request_interval_seconds,t.page_cap,m.slug
            FROM ingest.source_adapter a JOIN ingest.crawl_target t USING(source)
            JOIN ingest.market m ON m.id=t.market_id
            WHERE a.source IN ('grid-magazine','bartrams-garden','fleisher') ORDER BY a.source
        """)
        ).all()
        assert rows == [
            (
                source,
                "http" if source == "grid-magazine" else "stagehand",
                "jsonld",
                True,
                "reviewed",
                10,
                2,
                "philadelphia-pa",
            )
            for source in ("bartrams-garden", "fleisher", "grid-magazine")
        ]
    command.downgrade(config, "20260928_0015")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("""SELECT count(*) FROM ingest.source_adapter
            WHERE source IN ('grid-magazine','bartrams-garden','fleisher')""")
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.text("""SELECT count(*) FROM information_schema.columns
            WHERE table_schema='ingest' AND table_name='site_page'
            AND column_name='extraction_skips'""")
            )
            == 0
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM ingest.source_adapter")) == 8
    engine.dispose()
