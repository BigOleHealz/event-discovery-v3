from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from test_auth import NOW
from test_migrations import migration_config, table_names


def test_feedback_migration_round_trip_and_constraints(database_url: str) -> None:
    config = migration_config(database_url)
    engine = create_engine(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260928_0016")
    assert "event_feedback_request" not in table_names(engine)
    command.upgrade(config, "head")
    assert "event_feedback_request" in table_names(engine)
    attendance, user, event, run = [uuid4() for _ in range(4)]
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO app_user (id, google_sub) VALUES (:id, :sub)"),
                {"id": user, "sub": str(user)},
            )
            conn.execute(
                text("""
                INSERT INTO canonical_event (id,title,starts_at,timezone,location)
                VALUES (:id,'Feedback migration',:now,'UTC',
                        ST_SetSRID(ST_MakePoint(-75,40),4326)::geography)
            """),
                {"id": event, "now": NOW},
            )
            conn.execute(
                text("""
                INSERT INTO attendance (id,canonical_event_id,user_id,state)
                VALUES (:id,:event,:user,'attended')
            """),
                {"id": attendance, "event": event, "user": user},
            )
            conn.execute(
                text("INSERT INTO event_feedback_request VALUES (:id,:now)"),
                {"id": attendance, "now": NOW},
            )
            conn.execute(
                text("""
                INSERT INTO ingest.run (id,run_date,started_at,source,status,airflow_dag_id)
                VALUES (:id,:date,:now,'postgres','success','request_event_feedback')
            """),
                {"id": run, "date": NOW.date(), "now": NOW},
            )
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(
                text("INSERT INTO event_feedback_request VALUES (:id,:now)"),
                {"id": attendance, "now": NOW},
            )
        for rating in (0, 6):
            with pytest.raises(IntegrityError), engine.begin() as conn:
                conn.execute(
                    text("UPDATE attendance SET rating=:rating WHERE id=:id"),
                    {"id": attendance, "rating": rating},
                )
        with pytest.raises(IntegrityError):
            command.downgrade(config, "20260928_0016")
        assert "event_feedback_request" in table_names(engine)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM ingest.run WHERE id=:id"), {"id": run})
            conn.execute(text("DELETE FROM attendance WHERE id=:id"), {"id": attendance})
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM event_feedback_request WHERE attendance_id=:id"),
                    {"id": attendance},
                )
                == 0
            )
            conn.execute(text("DELETE FROM canonical_event WHERE id=:id"), {"id": event})
            conn.execute(text("DELETE FROM app_user WHERE id=:id"), {"id": user})
        command.downgrade(config, "20260928_0016")
        command.upgrade(config, "head")
        engine.dispose()
