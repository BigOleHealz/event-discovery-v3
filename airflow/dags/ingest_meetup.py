"""Nightly official Meetup GraphQL ingestion DAG."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from airflow.sdk import dag, get_current_context, task

from ingestion.clock import utc_now
from ingestion.database import IngestionRepository
from ingestion.meetup import MeetupClient, MeetupConfig, meetup_geo_radius
from ingestion.models import MeetupSearchTarget
from ingestion.pipeline import (
    crawl_and_stage_meetup,
    fail_run_on_error,
    group_crawl_targets_by_market,
    parse_and_filter_staged,
)

DAG_ID = "ingest_meetup"


def _repository() -> IngestionRepository:
    database_url = os.environ.get("EVENT_DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("EVENT_DATABASE_URL is required")
    return IngestionRepository(database_url)


@dag(
    dag_id=DAG_ID,
    schedule=os.environ.get("MEETUP_DAG_SCHEDULE", "0 3 * * *"),
    start_date=datetime(2026, 8, 28, tzinfo=UTC),
    catchup=False,
    tags=["ingestion", "meetup"],
)
def build_ingest_meetup():
    """Build one independently mapped Meetup flow per canonical market."""

    @task
    def configured_markets() -> list[dict[str, object]]:
        groups = group_crawl_targets_by_market(
            _repository().enabled_crawl_targets(source="meetup")
        )
        if not groups:
            raise ValueError("No enabled Meetup crawl targets are configured")
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
        config = MeetupConfig.from_env()
        started_at = utc_now()
        raw_targets = cast(list[dict[str, object]], market_config["targets"])
        if not raw_targets:
            raise ValueError("A market crawl must contain at least one target")
        market_id = uuid.UUID(str(market_config["market_id"]))
        run_id = _repository().open_run(
            dag_id=DAG_ID,
            airflow_run_id=airflow_run_id,
            source="meetup",
            source_url=config.api_url,
            market_id=market_id,
            categories=tuple(str(target["category"]) for target in raw_targets),
            window_start=started_at.date(),
            window_end=started_at.date()
            + timedelta(days=max(int(target["window_days"]) for target in raw_targets) - 1),
            started_at=started_at,
        )
        return {**market_config, "run_id": str(run_id), "started_at": started_at.isoformat()}

    @task
    def fetch_pages(run_context: dict[str, object]) -> dict[str, object]:
        run_id = uuid.UUID(str(run_context["run_id"]))
        repository = _repository()
        config = MeetupConfig.from_env()
        market_timezone = ZoneInfo(str(run_context["market_timezone"]))
        local_date = datetime.fromisoformat(str(run_context["started_at"])).astimezone(
            market_timezone
        ).date()
        raw_targets = cast(list[dict[str, object]], run_context["targets"])
        targets: list[MeetupSearchTarget] = []
        for target in raw_targets:
            location = meetup_geo_radius(
                cast(dict[str, object], target["source_location"])
            )
            window_days = int(target["window_days"])
            targets.append(
                MeetupSearchTarget(
                    crawl_target_id=uuid.UUID(str(target["id"])),
                    latitude=location.latitude,
                    longitude=location.longitude,
                    radius_miles=location.radius_miles,
                    topic_category_id=str(target["category"]),
                    window_start=datetime.combine(local_date, time.min, market_timezone),
                    window_end=datetime.combine(
                        local_date + timedelta(days=window_days - 1),
                        time.max,
                        market_timezone,
                    ),
                    page_cap=int(target["page_cap"]),
                )
            )
        with fail_run_on_error(repository=repository, run_id=run_id, clock=utc_now):
            with MeetupClient(config) as client:
                summary = crawl_and_stage_meetup(
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
            "events_found": summary.unique_ids,
        }

    @task
    def parse_listings(crawl_context: dict[str, object]) -> dict[str, object]:
        run_id = uuid.UUID(str(crawl_context["run_id"]))
        repository = _repository()
        with fail_run_on_error(repository=repository, run_id=run_id, clock=utc_now):
            summary = parse_and_filter_staged(
                repository=repository,
                run_id=run_id,
                clock=utc_now,
                source="meetup",
            )
            if summary.processed_total != int(crawl_context["events_found"]):
                raise ValueError(
                    f"processed {summary.processed_total} staged listings but staged "
                    f"{crawl_context['events_found']} unique Meetup events"
                )
        return {
            **crawl_context,
            "accepted": len(summary.accepted),
            "rejected_online": summary.rejected_online,
            "rejected_no_location": summary.rejected_no_location,
        }

    @task
    def close_run(summary: dict[str, object]) -> None:
        _repository().mark_success(
            run_id=uuid.UUID(str(summary["run_id"])),
            events_found=int(summary["events_found"]),
            listing_appearances=int(summary["listing_appearances"]),
            events_rejected_online=int(summary["rejected_online"]),
            finished_at=utc_now(),
        )

    markets = configured_markets()
    run_contexts = open_run.expand(market_config=markets)
    crawl_contexts = fetch_pages.expand(run_context=run_contexts)
    parsed_contexts = parse_listings.expand(crawl_context=crawl_contexts)
    close_run.expand(summary=parsed_contexts)


ingest_meetup = build_ingest_meetup()
