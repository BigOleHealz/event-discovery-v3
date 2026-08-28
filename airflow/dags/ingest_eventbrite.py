"""Nightly two-stage Eventbrite public-discovery ingestion DAG."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast

from airflow.sdk import dag, get_current_context, task

from ingestion.clock import utc_now
from ingestion.database import IngestionRepository
from ingestion.eventbrite import (
    ApiEventDetailFetcher,
    EventbriteConfig,
    EventbriteListingClient,
)
from ingestion.models import EventbriteEventReference, EventbriteSearchTarget
from ingestion.pipeline import (
    crawl_eventbrite_location,
    fail_run_on_error,
    fetch_and_stage_eventbrite_details,
    parse_and_filter_staged,
)

DAG_ID = "ingest_eventbrite"


def _repository() -> IngestionRepository:
    database_url = os.environ.get("EVENT_DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("EVENT_DATABASE_URL is required")
    return IngestionRepository(database_url)


@dag(
    dag_id=DAG_ID,
    schedule=os.environ.get("EVENTBRITE_DAG_SCHEDULE", "15 3 * * *"),
    start_date=datetime(2026, 8, 27, tzinfo=UTC),
    catchup=False,
    tags=["ingestion", "eventbrite"],
)
def build_ingest_eventbrite():
    """Build one independently mapped ingestion flow per configured location."""

    @task
    def configured_locations() -> list[str]:
        return list(EventbriteConfig.from_env().locations)

    @task
    def open_run(location: str) -> dict[str, object]:
        context = get_current_context()
        airflow_run_id = str(context["run_id"])
        config = EventbriteConfig.from_env()
        started_at = utc_now()
        window_start = started_at.date()
        window_end = window_start + timedelta(days=config.window_days - 1)
        source_url = f"{config.web_base_url}/d/{location}/"
        run_id = _repository().open_run(
            dag_id=DAG_ID,
            airflow_run_id=airflow_run_id,
            source_url=source_url,
            city=location,
            categories=config.categories,
            window_start=window_start,
            window_end=window_end,
            started_at=started_at,
        )
        return {
            "run_id": str(run_id),
            "location": location,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }

    @task
    def fetch_pages(run_context: dict[str, object]) -> dict[str, object]:
        run_id = uuid.UUID(str(run_context["run_id"]))
        repository = _repository()
        config = EventbriteConfig.from_env()
        location = str(run_context["location"])
        window_start = date.fromisoformat(str(run_context["window_start"]))
        window_end = date.fromisoformat(str(run_context["window_end"]))
        targets = tuple(
            EventbriteSearchTarget(
                location=location,
                category=category,
                window_start=window_start,
                window_end=window_end,
            )
            for category in config.categories
        )
        with fail_run_on_error(repository=repository, run_id=run_id, clock=utc_now):
            with EventbriteListingClient(config) as client:
                summary = crawl_eventbrite_location(
                    client=client,
                    targets=targets,
                    repository=repository,
                    run_id=run_id,
                    clock=utc_now,
                )
        return {
            **run_context,
            "pages_fetched": summary.pages_fetched,
            "listing_appearances": summary.listing_appearances,
            "event_references": [
                {"event_id": reference.event_id, "canonical_url": reference.canonical_url}
                for reference in summary.event_references
            ],
        }

    @task
    def collect_ids(crawl_context: dict[str, object]) -> dict[str, object]:
        references = cast(list[dict[str, str]], crawl_context["event_references"])
        unique = {reference["event_id"]: reference for reference in references}
        return {
            **crawl_context,
            "event_references": list(unique.values()),
            "events_found": len(unique),
        }

    @task
    def fetch_details(crawl_context: dict[str, object]) -> dict[str, object]:
        run_id = uuid.UUID(str(crawl_context["run_id"]))
        repository = _repository()
        config = EventbriteConfig.from_env()
        raw_references = cast(list[dict[str, str]], crawl_context["event_references"])
        references = tuple(
            EventbriteEventReference(
                event_id=reference["event_id"],
                canonical_url=reference["canonical_url"],
            )
            for reference in raw_references
        )
        with fail_run_on_error(repository=repository, run_id=run_id, clock=utc_now):
            with ApiEventDetailFetcher(config) as fetcher:
                summary = fetch_and_stage_eventbrite_details(
                    event_references=references,
                    fetcher=fetcher,
                    repository=repository,
                    run_id=run_id,
                    clock=utc_now,
                    cache_ttl=timedelta(hours=config.detail_cache_ttl_hours),
                )
        return {
            **crawl_context,
            "staged": summary.staged,
            "detail_fetched": summary.fetched,
            "detail_cached": summary.cached,
            "partial": summary.partial,
            "partial_reason": summary.partial_reason,
        }

    @task
    def parse_listings(detail_context: dict[str, object]) -> dict[str, object]:
        run_id = uuid.UUID(str(detail_context["run_id"]))
        repository = _repository()
        with fail_run_on_error(repository=repository, run_id=run_id, clock=utc_now):
            summary = parse_and_filter_staged(repository=repository, run_id=run_id, clock=utc_now)
            if summary.processed_total != int(detail_context["staged"]):
                raise ValueError(
                    f"processed {summary.processed_total} staged listings but staged "
                    f"{detail_context['staged']} details"
                )
        return {
            **detail_context,
            "accepted": len(summary.accepted),
            "rejected_online": summary.rejected_online,
            "rejected_no_location": summary.rejected_no_location,
        }

    @task
    def close_run(summary: dict[str, object]) -> None:
        repository = _repository()
        arguments = {
            "run_id": uuid.UUID(str(summary["run_id"])),
            "events_found": int(summary["events_found"]),
            "listing_appearances": int(summary["listing_appearances"]),
            "detail_fetched": int(summary["detail_fetched"]),
            "detail_cached": int(summary["detail_cached"]),
            "events_rejected_online": int(summary["rejected_online"]),
            "finished_at": utc_now(),
        }
        if bool(summary["partial"]):
            repository.mark_partial(
                **arguments,
                reason=str(summary["partial_reason"] or "Eventbrite detail crawl incomplete"),
            )
        else:
            repository.mark_success(**arguments)

    locations = configured_locations()
    run_contexts = open_run.expand(location=locations)
    crawl_contexts = fetch_pages.expand(run_context=run_contexts)
    collected_contexts = collect_ids.expand(crawl_context=crawl_contexts)
    detail_contexts = fetch_details.expand(crawl_context=collected_contexts)
    parsed_contexts = parse_listings.expand(detail_context=detail_contexts)
    close_run.expand(summary=parsed_contexts)


ingest_eventbrite = build_ingest_eventbrite()
