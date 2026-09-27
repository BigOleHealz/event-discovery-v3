import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError
from test_migrations import migration_config


def test_taxonomy_constraints_and_round_trip(database_url: str) -> None:
    config = migration_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    for sql in (
        "UPDATE category SET parent_id = 'bebop' WHERE id = 'music'",
        "UPDATE category SET parent_id = 'jazz' WHERE id = 'jazz'",
        "UPDATE category SET parent_id = 'absent' WHERE id = 'jazz'",
        "INSERT INTO category_alias VALUES ('music', 'jazz')",
        "INSERT INTO category_alias VALUES ('UPPERCASE', 'jazz')",
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(sa.text(sql))
    command.downgrade(config, "20260926_0011")
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT to_regclass('category')")) is None
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(sa.text("SELECT parent_id FROM category WHERE id = 'bebop'"))
            == "jazz"
        )
    engine.dispose()
