import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError
from test_migrations import migration_config


def test_enablement_requires_review_and_migration_round_trip(database_url: str) -> None:
    config = migration_config(database_url)
    command.upgrade(config, "head")
    engine = sa.create_engine(database_url)
    for assignment in (
        "access_policy=NULL",
        "access_policy=jsonb_set(access_policy,'{status}','\"pending\"')",
        "access_policy=jsonb_set(access_policy,'{status}','\"blocked\"')",
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                sa.text(f"UPDATE ingest.source_adapter SET {assignment} WHERE source='philamoca'")
            )
    with engine.begin() as connection:
        # Disabling and recording a blocked review is one atomic operational change.
        connection.execute(
            sa.text("""UPDATE ingest.source_adapter SET enabled=false,
            access_policy=jsonb_set(access_policy,'{status}','\"blocked\"')
            WHERE source='philamoca'""")
        )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE ingest.source_adapter SET enabled=true WHERE source='philamoca'")
        )
    command.downgrade(config, "20260927_0014")
    with engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE ingest.source_adapter SET enabled=true WHERE source='philamoca'")
        )
        connection.execute(
            sa.text("""UPDATE ingest.source_adapter SET access_policy=
            jsonb_set(access_policy,'{status}','\"reviewed\"') WHERE source='philamoca'""")
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(sa.text("SELECT count(*) FROM ingest.source_adapter WHERE enabled"))
            == 5
        )
    engine.dispose()
