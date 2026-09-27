"""Model-configured site ingestion; source/market differences are database inventory."""

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import DAG, dag, get_current_context, task

from ingestion.clock import utc_now
from ingestion.site_jobs import (
    close_site_run,
    collect_site_ids,
    configured_sites,
    fetch_site_pages,
    open_site_run,
    parse_site_events,
    stage_site_events,
)


def database_url() -> str:
    value = os.environ.get("EVENT_DATABASE_URL", "").strip()
    if not value:
        raise ValueError("EVENT_DATABASE_URL is required")
    return value


def build_site_dag(dag_id: str, method: str, schedule: str) -> DAG:
    @dag(
        dag_id=dag_id,
        schedule=schedule,
        start_date=datetime(2026, 9, 27, tzinfo=UTC),
        catchup=False,
        max_active_runs=1,
        default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
        tags=["ingestion", "sites", method],
    )
    def flow() -> None:
        @task
        def configured_targets() -> list[dict[str, object]]:
            return configured_sites(database_url(), method)

        @task
        def open_run(config: dict[str, object]) -> dict[str, object]:
            return open_site_run(
                database_url(), config, dag_id, str(get_current_context()["run_id"]), utc_now
            )

        @task
        def fetch_pages(context: dict[str, object]) -> dict[str, object]:
            return fetch_site_pages(database_url(), context, utc_now)

        @task
        def collect_ids(context: dict[str, object]) -> dict[str, object]:
            return collect_site_ids(database_url(), context)

        @task
        def fetch_details(context: dict[str, object]) -> dict[str, object]:
            # No separate detail endpoint: stage deduplicated extraction payloads.
            return stage_site_events(database_url(), context, utc_now)

        @task
        def parse_listings(context: dict[str, object]) -> dict[str, object]:
            return parse_site_events(database_url(), context, utc_now)

        @task
        def close_run(context: dict[str, object]) -> None:
            close_site_run(database_url(), context, utc_now)

        configs = configured_targets()
        opened = open_run.expand(config=configs)
        fetched = fetch_pages.expand(context=opened)
        collected = collect_ids.expand(context=fetched)
        staged = fetch_details.expand(context=collected)
        parsed = parse_listings.expand(context=staged)
        close_run.expand(context=parsed)

    return flow()


ingest_site_stagehand = build_site_dag(
    "ingest_site_stagehand",
    "stagehand",
    os.environ.get("SITE_STAGEHAND_DAG_SCHEDULE", "0 4 * * *"),
)
ingest_site_generic = build_site_dag(
    "ingest_site_generic",
    "http",
    os.environ.get("SITE_GENERIC_DAG_SCHEDULE", "30 3 * * *"),
)
