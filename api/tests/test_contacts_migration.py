from alembic import command
from sqlalchemy import create_engine, text
from test_migrations import migration_config, table_names


def test_contacts_migration_round_trip(database_url: str) -> None:
    config = migration_config(database_url)
    engine = create_engine(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260928_0017")
    assert "sms_delivery" not in table_names(engine)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT to_regprocedure('match_contacts_to_users()')")) is None
        )
    command.upgrade(config, "head")
    assert "sms_delivery" in table_names(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT match_contacts_to_users()")) == 0
    engine.dispose()
