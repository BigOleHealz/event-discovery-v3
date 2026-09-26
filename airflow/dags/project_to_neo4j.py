"""Rebuild the disposable graph from Postgres (Phase 5a)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, task

from ingestion.clock import utc_now
from ingestion.graph import GraphConfig, project_to_neo4j


@dag(
    dag_id="project_to_neo4j",
    schedule=os.environ.get("NEO4J_PROJECTION_DAG_SCHEDULE", "30 * * * *"),
    start_date=datetime(2026, 9, 26, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
    tags=["ingestion", "graph"],
)
def build_project_to_neo4j() -> None:
    @task
    def rebuild_projection(airflow_run_id: str) -> dict[str, int]:
        database_url = os.environ.get("EVENT_DATABASE_URL", "").strip()
        if not database_url:
            raise ValueError("EVENT_DATABASE_URL is required")
        return project_to_neo4j(
            database_url, GraphConfig.from_env(), airflow_run_id=airflow_run_id, clock=utc_now
        )

    rebuild_projection("{{ run_id }}")


project_to_neo4j_dag = build_project_to_neo4j()
