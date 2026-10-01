"""Repair contact-to-account matches with audited, retry-safe runs (6d)."""

from uuid import uuid5

import psycopg

from ingestion.clock import Clock
from ingestion.database import RUN_NAMESPACE


def match_contacts_to_users(
    database_url: str, *, airflow_run_id: str, clock: Clock
) -> dict[str, int]:
    now = clock()
    run_id = uuid5(RUN_NAMESPACE, f"match_contacts_to_users:{airflow_run_id}")
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(
            "SELECT pg_advisory_lock(hashtextextended('match_contacts_to_users', 0))"
        )
        connection.execute(
            """
            INSERT INTO ingest.run (id, run_date, source, started_at, status,
                                    airflow_dag_id, airflow_run_id)
            VALUES (%s, %s, 'postgres', %s, 'running', 'match_contacts_to_users', %s)
            ON CONFLICT (id) DO UPDATE SET started_at=EXCLUDED.started_at,
                status='running', finished_at=NULL, error_message=NULL, events_found=0
        """,
            (run_id, now.date(), now, airflow_run_id),
        )
        try:
            with connection.transaction():
                result = connection.execute("SELECT match_contacts_to_users()").fetchone()
                changed = int(result[0]) if result else 0
                connection.execute(
                    """
                    UPDATE ingest.run SET status='success', finished_at=%s, events_found=%s
                    WHERE id=%s
                """,
                    (clock(), changed, run_id),
                )
            return {"matched": changed}
        except Exception as error:
            connection.execute(
                """
                UPDATE ingest.run SET status='failed', finished_at=%s, error_message=%s
                WHERE id=%s
            """,
                (clock(), type(error).__name__, run_id),
            )
            raise
