import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError
from test_migrations import migration_config


def test_site_inventory_constraints_and_round_trip(database_url: str) -> None:
    config = migration_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        assert connection.execute(
            sa.text("""
            SELECT source, fetch_method, priority FROM ingest.source_adapter ORDER BY priority
        """)
        ).all() == [("eventbrite", "api", 0), ("meetup", "api", 1)]
    for sql in (
        "INSERT INTO ingest.source_adapter(source,fetch_method,priority) "
        "VALUES('eventbrite','api',3)",
        "INSERT INTO ingest.source_adapter(source,fetch_method,priority) VALUES('bad','other',3)",
        "INSERT INTO ingest.source_adapter(source,fetch_method,priority) VALUES('bad','http',3)",
        """INSERT INTO ingest.source_adapter
            (source,fetch_method,priority,extraction,pagination,model,prompt_version,enabled)
            VALUES('bad','http',3,'{}','{}','model',0,true)""",
        """INSERT INTO ingest.extraction_plan
            (source,config_hash,plan,model,prompt_version,derived_at)
            VALUES('unknown','hash','{}','model',1,now())""",
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(sa.text(sql))
    command.downgrade(config, "20260926_0012")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT to_regclass('ingest.source_adapter')")) is None
        assert connection.scalar(sa.text("SELECT to_regclass('ingest.extraction_plan')")) is None
        assert connection.scalar(sa.text("SELECT to_regclass('ingest.site_page')")) is None
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM ingest.source_adapter")) == 2
    engine.dispose()
