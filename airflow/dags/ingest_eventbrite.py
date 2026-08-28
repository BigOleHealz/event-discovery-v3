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
    eventbrite_location_slug,
)
from ingestion.models import EventbriteEventReference, EventbriteSearchTarget
from ingestion.pipeline import (
    crawl_eventbrite_location,
    fail_run_on_error,
    fetch_and_stage_eventbrite_details,
    group_crawl_targets_by_market,
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
    """Build one independently mapped ingestion flow per configured market."""

    @task
    def configured_markets() -> list[dict[str, object]]:
        groups = group_crawl_targets_by_market(
            _repository().enabled_crawl_targets(source="eventbrite")
        )
        if not groups:
            raise ValueError("No enabled Eventbrite crawl targets are configured")
        return [
            {
                "source": group.source,
                "market_id": str(group.market_id),
                "market_slug": group.market_slug,
                "market_name": group.market_name,
                "market_timezone": group.market_timezone,
                "targets": [
                    {
                        "id": str(target.id),
                        "source_location": dict(target.source_location),
                        "category": target.category,
                        "window_days": target.window_days,
                        "page_cap": target.page_cap,
                    }
                    for target in group.targets
                ],
            }
            for group in groups
        ]

    @task
    def open_run(market_config: dict[str, object]) -> dict[str, object]:
        context = get_current_context()
        airflow_run_id = str(context["run_id"])
        config = EventbriteConfig.from_env()
        started_at = utc_now()
        window_start = started_at.date()
        raw_targets = cast(list[dict[str, object]], market_config["targets"])
        if not raw_targets:
            raise ValueError("A market crawl must contain at least one target")
        window_end = window_start + timedelta(
            days=max(int(target["window_days"]) for target in raw_targets) - 1
        )
        source_locations = [
            eventbrite_location_slug(cast(dict[str, object], target["source_location"]))
            for target in raw_targets
        ]
        source_url = (
            f"{config.web_base_url}/d/{source_locations[0]}/"
            if len(set(source_locations)) == 1
            else config.web_base_url
        )
        market_id = uuid.UUID(str(market_config["market_id"]))
        run_id = _repository().open_run(
            dag_id=DAG_ID,
            airflow_run_id=airflow_run_id,
            source="eventbrite",
            source_url=source_url,
            market_id=market_id,
            categories=tuple(str(target["category"]) for target in raw_targets),
            window_start=window_start,
            window_end=window_end,
            started_at=started_at,
        )
        return {
            **market_config,
            "run_id": str(run_id),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        }

    @task
    def fetch_pages(run_context: dict[str, object]) -> dict[str, object]:
        run_id = uuid.UUID(str(run_context["run_id"]))
        repository = _repository()
        config = EventbriteConfig.from_env()
        window_start = date.fromisoformat(str(run_context["window_start"]))
        raw_targets = cast(list[dict[str, object]], run_context["targets"])
        targets = tuple(
            EventbriteSearchTarget(
                crawl_target_id=uuid.UUID(str(target["id"])),
                location_slug=eventbrite_location_slug(
                    cast(dict[str, object], target["source_location"])
                ),
                category=str(target["category"]),
                window_start=window_start,
                window_end=window_start + timedelta(days=int(target["window_days"]) - 1),
                page_cap=int(target["page_cap"]),
            )
            for target in raw_targets
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

    markets = configured_markets()
    run_contexts = open_run.expand(market_config=markets)
    crawl_contexts = fetch_pages.expand(run_context=run_contexts)
    collected_contexts = collect_ids.expand(crawl_context=crawl_contexts)
    detail_contexts = fetch_details.expand(crawl_context=collected_contexts)
    parsed_contexts = parse_listings.expand(detail_context=detail_contexts)
    close_run.expand(summary=parsed_contexts)


ingest_eventbrite = build_ingest_eventbrite()
