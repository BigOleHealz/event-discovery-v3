"""Idempotent in-app feedback prompts, with all attendance state owned by Postgres."""

from uuid import uuid5

import psycopg

from ingestion.clock import Clock
from ingestion.database import RUN_NAMESPACE

# A local calendar-day boundary avoids prompting during overnight/multiday events.
# Unknown end times become eligible the day after their start, without inventing a duration.
ELAPSED_EVENT = """
    (COALESCE(e.ends_at, e.starts_at) AT TIME ZONE e.timezone)::date
        < (%(now)s::timestamptz AT TIME ZONE e.timezone)::date
"""


def request_event_feedback(
    database_url: str, *, airflow_run_id: str, clock: Clock
) -> dict[str, int]:
    now = clock()
    run_id = uuid5(RUN_NAMESPACE, f"request_event_feedback:{airflow_run_id}")
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(hashtextextended('request_event_feedback', 0))")
        connection.execute(
            """
            INSERT INTO ingest.run (id, run_date, source, started_at, status,
                                    airflow_dag_id, airflow_run_id)
            VALUES (%s, %s, 'postgres', %s, 'running', 'request_event_feedback', %s)
            ON CONFLICT (id) DO UPDATE SET started_at=EXCLUDED.started_at,
                status='running', finished_at=NULL, error_message=NULL, events_found=0
        """,
            (run_id, now.date(), now, airflow_run_id),
        )
        try:
            with connection.transaction():
                transitioned = connection.execute(
                    f"""
                    UPDATE attendance a SET state='attended'
                    FROM canonical_event e WHERE e.id=a.canonical_event_id
                        AND a.state='attending' AND {ELAPSED_EVENT}
                """,
                    {"now": now},
                ).rowcount
                queued = connection.execute(
                    f"""
                    INSERT INTO event_feedback_request (attendance_id, requested_at)
                    SELECT a.id, %(now)s FROM attendance a
                    JOIN canonical_event e ON e.id=a.canonical_event_id
                    JOIN app_user u ON u.id=a.user_id
                    WHERE a.state='attended' AND a.feedback_at IS NULL
                        AND u.is_shadow IS FALSE AND u.google_sub IS NOT NULL
                        AND {ELAPSED_EVENT}
                    ON CONFLICT (attendance_id) DO NOTHING
                """,
                    {"now": now},
                ).rowcount
                connection.execute(
                    """
                    UPDATE ingest.run SET status='success', finished_at=%s, events_found=%s
                    WHERE id=%s
                """,
                    (clock(), queued, run_id),
                )
            return {"attended": transitioned, "requested": queued}
        except Exception as error:
            connection.execute(
                """
                UPDATE ingest.run SET status='failed', finished_at=%s, error_message=%s
                WHERE id=%s
            """,
                (clock(), type(error).__name__, run_id),
            )
            raise
