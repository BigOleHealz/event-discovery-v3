"""Embed and resolve pending geocoded occurrences (Phase 4d)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, task

from ingestion.clock import utc_now
from ingestion.dedup import dedup_pending
from ingestion.embeddings import EmbeddingClient, EmbeddingConfig, embed_pending


def database_url() -> str:
    value = os.environ.get("EVENT_DATABASE_URL", "").strip()
    if not value:
        raise ValueError("EVENT_DATABASE_URL is required")
    return value


@dag(
    dag_id="dedup_pending",
    schedule=os.environ.get("DEDUP_DAG_SCHEDULE", "15 * * * *"),
    start_date=datetime(2026, 9, 16, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1)},
    tags=["ingestion", "dedup"],
)
def build_dedup_pending() -> None:
    @task
    def embed_listings() -> int:
        with EmbeddingClient(EmbeddingConfig.from_env()) as client:
            return embed_pending(database_url(), client)

    @task
    def resolve_listings() -> dict[str, int]:
        priority = tuple(
            source.strip()
            for source in os.environ.get("DEDUP_SOURCE_PRIORITY", "eventbrite,meetup").split(",")
            if source.strip()
        )
        return dedup_pending(database_url(), clock=utc_now, source_priority=priority)

    embed_listings() >> resolve_listings()


dedup_pending_dag = build_dedup_pending()
