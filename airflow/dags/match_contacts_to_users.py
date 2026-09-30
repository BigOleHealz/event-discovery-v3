"""Match imported contacts to registered users (Phase 6d)."""

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, task

from ingestion.clock import utc_now
from ingestion.contacts import match_contacts_to_users


@dag(
    dag_id="match_contacts_to_users",
    schedule=os.environ.get("CONTACT_MATCH_DAG_SCHEDULE", "0 2 * * *"),
    start_date=datetime(2026, 9, 28, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
    tags=["social", "contacts"],
)
def build_match_contacts_to_users() -> None:
    @task
    def match_contacts(airflow_run_id: str) -> dict[str, int]:
        database_url = os.environ.get("EVENT_DATABASE_URL", "").strip()
        if not database_url:
            raise ValueError("EVENT_DATABASE_URL is required")
        return match_contacts_to_users(database_url, airflow_run_id=airflow_run_id, clock=utc_now)

    match_contacts("{{ run_id }}")


match_contacts_to_users_dag = build_match_contacts_to_users()
