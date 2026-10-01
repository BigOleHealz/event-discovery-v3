"""Offer past attendees an in-app feedback prompt (Phase 6c)."""

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, task

from ingestion.clock import utc_now
from ingestion.feedback import request_event_feedback


@dag(
    dag_id="request_event_feedback",
    schedule=os.environ.get("EVENT_FEEDBACK_DAG_SCHEDULE", "0 10 * * *"),
    start_date=datetime(2026, 9, 28, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
    tags=["social", "feedback"],
)
def build_request_event_feedback() -> None:
    @task
    def queue_feedback(airflow_run_id: str) -> dict[str, int]:
        database_url = os.environ.get("EVENT_DATABASE_URL", "").strip()
        if not database_url:
            raise ValueError("EVENT_DATABASE_URL is required")
        return request_event_feedback(database_url, airflow_run_id=airflow_run_id, clock=utc_now)

    queue_feedback("{{ run_id }}")


request_event_feedback_dag = build_request_event_feedback()
