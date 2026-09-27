"""Task implementations kept importable without loading Airflow."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import TypedDict, cast

import httpx
import psycopg
from psycopg.types.json import Jsonb

from ingestion.clock import Clock
from ingestion.database import IngestionRepository
from ingestion.models import CrawlTarget
from ingestion.pipeline import (
    fail_run_on_error,
    group_crawl_targets_by_market,
    parse_and_filter_staged,
)
from ingestion.site_extraction import PlanModel
from ingestion.site_models import SiteAdapter
from ingestion.site_pipeline import SiteClient, SiteProcessConfig, crawl_site
from ingestion.site_repository import SiteRepository


def configured_sites(database_url: str, method: str) -> list[dict[str, object]]:
    repository = IngestionRepository(database_url)
    groups: list[dict[str, object]] = []
    for adapter in SiteRepository(database_url).adapters(method):
        for group in group_crawl_targets_by_market(
            repository.enabled_crawl_targets(source=adapter.source)
        ):
            targets = [
                {**asdict(target), "id": str(target.id), "market_id": str(target.market_id)}
                for target in group.targets
            ]
            groups.append({"adapter": asdict(adapter), "targets": targets})
    return groups


def adapter_from(context: dict[str, object]) -> SiteAdapter:
    value = cast(dict[str, object], context["adapter"])
    return SiteAdapter(
        str(value["source"]),
        str(value["fetch_method"]),
        cast(dict[str, object], value["extraction"]),
        cast(dict[str, object], value["pagination"]),
        str(value["model"]),
        int(str(value["prompt_version"])),
    )


def targets_from(context: dict[str, object]) -> list[CrawlTarget]:
    return [
        CrawlTarget(
            id=uuid.UUID(str(target["id"])),
            source=str(target["source"]),
            market_id=uuid.UUID(str(target["market_id"])),
            market_slug=str(target["market_slug"]),
            market_name=str(target["market_name"]),
            market_timezone=str(target["market_timezone"]),
            source_location=cast(dict[str, object], target["source_location"]),
            category=str(target["category"]),
            window_days=int(str(target["window_days"])),
            page_cap=int(str(target["page_cap"])),
        )
        for target in cast(list[dict[str, object]], context["targets"])
    ]


def open_site_run(
    database_url: str,
    context: dict[str, object],
    dag_id: str,
    execution_id: str,
    clock: Clock,
) -> dict[str, object]:
    adapter = adapter_from(context)
    targets = targets_from(context)
    started = clock()
    run_id = IngestionRepository(database_url).open_run(
        dag_id=dag_id,
        airflow_run_id=execution_id,
        source=adapter.source,
        source_url=str(targets[0].source_location["url"]),
        market_id=targets[0].market_id,
        started_at=started,
        categories=tuple(target.category for target in targets),
    )
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        connection.execute(
            "UPDATE ingest.run SET source_config=%s WHERE id=%s", (Jsonb(context), run_id)
        )
    return {**context, "run_id": str(run_id), "started_at": started.isoformat()}


def fetch_site_pages(
    database_url: str,
    context: dict[str, object],
    clock: Clock,
) -> dict[str, object]:
    repository = IngestionRepository(database_url)
    run_id = uuid.UUID(str(context["run_id"]))
    with fail_run_on_error(repository=repository, run_id=run_id, clock=clock):
        config = SiteProcessConfig.from_env()
        with httpx.Client(timeout=config.timeout_seconds) as http:
            result = crawl_site(
                adapter=adapter_from(context),
                targets=targets_from(context),
                run_id=run_id,
                repository=SiteRepository(database_url),
                client=SiteClient(http, config),
                derive=PlanModel(
                    http,
                    config.extraction_api_url,
                    config.extraction_api_key,
                    config.max_model_input_bytes,
                ).derive,
                clock=clock,
            )
    return {**context, "appearances": result.appearances, "partial_reason": result.partial_reason}


def collect_site_ids(database_url: str, context: dict[str, object]) -> dict[str, object]:
    events = SiteRepository(database_url).staged_events(
        uuid.UUID(str(context["run_id"])), adapter_from(context).config_hash
    )
    return {**context, "events_found": len(events)}


def stage_site_events(
    database_url: str,
    context: dict[str, object],
    clock: Clock,
) -> dict[str, object]:
    run_id = uuid.UUID(str(context["run_id"]))
    repository = IngestionRepository(database_url)
    with fail_run_on_error(repository=repository, run_id=run_id, clock=clock):
        for payload in SiteRepository(database_url).staged_events(
            run_id, adapter_from(context).config_hash
        ):
            repository.stage_source_payload(
                source=adapter_from(context).source,
                run_id=run_id,
                payload=payload,
                seen_at=datetime.fromisoformat(str(context["started_at"])),
            )
    return context


def parse_site_events(
    database_url: str,
    context: dict[str, object],
    clock: Clock,
) -> dict[str, object]:
    repository = IngestionRepository(database_url)
    run_id = uuid.UUID(str(context["run_id"]))
    with fail_run_on_error(repository=repository, run_id=run_id, clock=clock):
        # Restage recorded payloads on retry: rejected listings were removed by the filter.
        stage_site_events(database_url, context, clock)
        parsed = parse_and_filter_staged(
            repository=repository,
            run_id=run_id,
            clock=clock,
            source=adapter_from(context).source,
        )
    return {**context, "rejected_online": parsed.rejected_online}


class RunResult(TypedDict):
    run_id: uuid.UUID
    events_found: int
    listing_appearances: int
    events_rejected_online: int
    detail_fetched: int
    detail_cached: int
    finished_at: datetime


def close_site_run(database_url: str, context: dict[str, object], clock: Clock) -> None:
    repository = IngestionRepository(database_url)
    values: RunResult = {
        "run_id": uuid.UUID(str(context["run_id"])),
        "events_found": int(str(context["events_found"])),
        "listing_appearances": int(str(context["appearances"])),
        "events_rejected_online": int(str(context["rejected_online"])),
        "detail_fetched": 0,
        "detail_cached": 0,
        "finished_at": clock(),
    }
    if context.get("partial_reason"):
        repository.mark_partial(**values, reason=str(context["partial_reason"]))
    else:
        repository.mark_success(**values)
