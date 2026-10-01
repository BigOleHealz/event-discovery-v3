from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
import pytest

from ingestion.feedback import request_event_feedback

NOW = datetime(2026, 9, 28, 1, tzinfo=UTC)


def uid(number: int) -> UUID:
    return UUID(int=6000 + number)


def pg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def seed_attendee(
    connection: psycopg.Connection[tuple[object, ...]],
    number: int,
    *,
    starts: datetime,
    ends: datetime | None,
    timezone: str = "UTC",
    state: str = "attending",
    shadow: bool = False,
) -> None:
    connection.execute(
        """
        INSERT INTO app_user (id,google_sub,is_shadow) VALUES (%s,%s,%s)
    """,
        (uid(100 + number), None if shadow else str(uid(100 + number)), shadow),
    )
    connection.execute(
        """
        INSERT INTO canonical_event (id,title,starts_at,ends_at,timezone,location)
        VALUES (%s,'Feedback event',%s,%s,%s,ST_SetSRID(ST_MakePoint(-75,40),4326)::geography)
    """,
        (uid(number), starts, ends, timezone),
    )
    connection.execute(
        """
        INSERT INTO attendance (id,canonical_event_id,user_id,state,source,created_at)
        VALUES (%s,%s,%s,%s,'invite_accept',%s)
    """,
        (uid(200 + number), uid(number), uid(100 + number), state, starts),
    )


@pytest.fixture
def feedback_db(database_url: str, clean_ingestion_tables: None) -> Iterator[str]:
    try:
        yield database_url
    finally:
        with psycopg.connect(pg_url(database_url)) as connection:
            connection.execute(
                "DELETE FROM attendance WHERE id BETWEEN %s AND %s", (uid(200), uid(220))
            )
            connection.execute(
                "DELETE FROM canonical_event WHERE id BETWEEN %s AND %s", (uid(0), uid(20))
            )
            connection.execute(
                "DELETE FROM app_user WHERE id BETWEEN %s AND %s", (uid(100), uid(120))
            )
            connection.execute(
                "DELETE FROM ingest.run WHERE airflow_dag_id='request_event_feedback'"
            )


def seed_cases(database_url: str) -> None:
    with psycopg.connect(pg_url(database_url)) as connection:
        for number in range(10):
            seed_attendee(
                connection,
                number,
                starts=NOW - timedelta(days=40),
                ends=(
                    None
                    if number == 2
                    else NOW + timedelta(days=1)
                    if number == 1
                    else NOW - timedelta(days=30)
                    if number == 9
                    else NOW - timedelta(hours=2)
                ),
                timezone="America/New_York"
                if number == 3
                else "Asia/Tokyo"
                if number == 5
                else "UTC",
                state="no_show" if number == 6 else "attended" if number == 8 else "attending",
                shadow=number == 7,
            )
        connection.execute(
            "UPDATE attendance SET rating=4,feedback_at=%s WHERE id=%s", (NOW, uid(208))
        )


def test_local_days_catch_up_and_retries_do_not_duplicate_requests(feedback_db: str) -> None:
    seed_cases(feedback_db)
    first = request_event_feedback(feedback_db, airflow_run_id="feedback-retry", clock=lambda: NOW)
    # Tokyo's end is already today, while UTC's is yesterday and New York's is still today.
    assert first == {"attended": 5, "requested": 4}
    assert request_event_feedback(
        feedback_db, airflow_run_id="feedback-retry", clock=lambda: NOW
    ) == {
        "attended": 0,
        "requested": 0,
    }
    with psycopg.connect(pg_url(feedback_db)) as connection:
        assert connection.execute(
            "SELECT attendance_id FROM event_feedback_request ORDER BY attendance_id"
        ).fetchall() == [
            (uid(200),),
            (uid(202),),
            (uid(204),),
            (uid(209),),
        ]
        assert connection.execute(
            "SELECT status,count(*) FROM ingest.run GROUP BY status"
        ).fetchall() == [("success", 1)]
        assert connection.execute(
            "SELECT state FROM attendance WHERE id=%s", (uid(207),)
        ).fetchone() == ("attended",)
        connection.execute(
            "UPDATE app_user SET is_shadow=false,google_sub='claimed' WHERE id=%s", (uid(107),)
        )
    assert request_event_feedback(
        feedback_db, airflow_run_id="feedback-claim", clock=lambda: NOW
    ) == {
        "attended": 0,
        "requested": 1,
    }


def test_dst_fallback_waits_for_next_local_calendar_day(feedback_db: str) -> None:
    with psycopg.connect(pg_url(feedback_db)) as connection:
        seed_attendee(
            connection,
            0,
            starts=datetime(2026, 11, 1, 4, tzinfo=UTC),
            ends=datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
            timezone="America/New_York",
        )
    before = datetime(2026, 11, 2, 4, 59, tzinfo=UTC)
    after = datetime(2026, 11, 2, 5, tzinfo=UTC)
    assert request_event_feedback(
        feedback_db, airflow_run_id="feedback-before", clock=lambda: before
    ) == {
        "attended": 0,
        "requested": 0,
    }
    assert request_event_feedback(
        feedback_db, airflow_run_id="feedback-after", clock=lambda: after
    ) == {
        "attended": 1,
        "requested": 1,
    }


def test_failed_enqueue_rolls_back_attendance_and_retry_repairs_run(feedback_db: str) -> None:
    seed_cases(feedback_db)
    with psycopg.connect(pg_url(feedback_db)) as connection:
        connection.execute(
            "ALTER TABLE event_feedback_request ADD CONSTRAINT fixture_reject CHECK (false)"
        )
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            request_event_feedback(feedback_db, airflow_run_id="feedback-failed", clock=lambda: NOW)
        with psycopg.connect(pg_url(feedback_db)) as connection:
            assert connection.execute(
                "SELECT state FROM attendance WHERE id=%s", (uid(200),)
            ).fetchone() == ("attending",)
            assert connection.execute("SELECT count(*) FROM event_feedback_request").fetchone() == (
                0,
            )
            assert connection.execute("SELECT status,error_message FROM ingest.run").fetchone() == (
                "failed",
                "CheckViolation",
            )
    finally:
        with psycopg.connect(pg_url(feedback_db)) as connection:
            connection.execute("ALTER TABLE event_feedback_request DROP CONSTRAINT fixture_reject")
    assert request_event_feedback(
        feedback_db, airflow_run_id="feedback-failed", clock=lambda: NOW
    ) == {
        "attended": 5,
        "requested": 4,
    }
    with psycopg.connect(pg_url(feedback_db)) as connection:
        assert connection.execute("SELECT status,error_message FROM ingest.run").fetchall() == [
            ("success", None)
        ]
