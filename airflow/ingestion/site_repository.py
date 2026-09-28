"""Persistent source inventory, page snapshots and extraction cache."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import httpx
import psycopg
from psycopg.types.json import Jsonb

from ingestion.clock import Clock
from ingestion.site_extraction import DerivePlan, ReplayResult, replay
from ingestion.site_models import SiteAdapter

PAGE_NAMESPACE = uuid.UUID("5f54c185-ec44-43f7-a6b7-47e9f485850d")


@dataclass(frozen=True)
class SitePage:
    id: uuid.UUID
    url: str
    html: str
    status: int


class SiteRepository:
    def __init__(self, database_url: str) -> None:
        self.url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def adapters(self, method: str) -> tuple[SiteAdapter, ...]:
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                """
                SELECT source, fetch_method, extraction, pagination, model, prompt_version
                FROM ingest.source_adapter WHERE enabled AND fetch_method = %s ORDER BY source
            """,
                (method,),
            ).fetchall()
        return tuple(SiteAdapter(*row) for row in rows)

    def page(self, page_id: uuid.UUID, config_hash: str) -> SitePage | None:
        with psycopg.connect(self.url) as connection:
            row = connection.execute(
                """
                SELECT pf.url, page.html, pf.http_status
                FROM ingest.site_page page
                JOIN ingest.page_fetch pf ON pf.id=page.page_fetch_id
                WHERE page.page_fetch_id=%s AND page.config_hash=%s AND pf.http_status < 400
            """,
                (page_id, config_hash),
            ).fetchone()
        return SitePage(page_id, *row) if row else None

    def save_page(
        self,
        *,
        page: SitePage,
        run_id: uuid.UUID,
        target_id: uuid.UUID,
        number: int,
        adapter: SiteAdapter,
        fetched_at: datetime,
        duration_ms: int,
        category: str,
    ) -> None:
        with psycopg.connect(self.url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.page_fetch
                    (id, run_id, crawl_target_id, url, page_number, fetch_method,
                     http_status, bytes, duration_ms, fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET url=EXCLUDED.url, http_status=EXCLUDED.http_status,
                    bytes=EXCLUDED.bytes, duration_ms=EXCLUDED.duration_ms,
                    fetched_at=EXCLUDED.fetched_at, error_message=NULL
            """,
                (
                    page.id,
                    run_id,
                    target_id,
                    page.url,
                    number,
                    adapter.fetch_method,
                    page.status,
                    len(page.html.encode()),
                    duration_ms,
                    fetched_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO ingest.site_page (page_fetch_id, config_hash, html, category)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT(page_fetch_id) DO UPDATE SET html=EXCLUDED.html, events=NULL,
                    config_hash=EXCLUDED.config_hash
            """,
                (page.id, adapter.config_hash, page.html, category),
            )

    def extract(
        self,
        page: SitePage,
        adapter: SiteAdapter,
        timezone: str,
        derive: DerivePlan,
        clock: Clock,
    ) -> ReplayResult:
        failure: Exception | None = None
        result: ReplayResult | None = None
        with psycopg.connect(self.url) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"site-plan:{adapter.source}:{adapter.config_hash}",),
            )
            completed = connection.execute(
                """
                SELECT page.events, page.next_url, page.next_selector, pf.error_message,
                       page.extraction_skips
                FROM ingest.site_page page JOIN ingest.page_fetch pf ON pf.id=page.page_fetch_id
                WHERE page_fetch_id=%s
            """,
                (page.id,),
            ).fetchone()
            if completed and completed[3] == "extraction_validation_failed":
                raise ValueError("site extraction already failed validation for this run")
            if completed and completed[0] is not None:
                return ReplayResult(completed[0], completed[1], completed[2], completed[4])
            cached = connection.execute(
                """
                SELECT plan, model FROM ingest.extraction_plan WHERE source=%s AND config_hash=%s
            """,
                (adapter.source, adapter.config_hash),
            ).fetchone()
            failures = 0
            cache_hit = False
            model = adapter.model
            if cached:
                try:
                    result = replay(cached[0], page.html, page.url, adapter, timezone)
                    model = cached[1]
                    cache_hit = True
                except Exception:
                    # The same validator protects model output and database-cached plans.
                    failures += 1
                    connection.execute(
                        """
                        DELETE FROM ingest.extraction_plan WHERE source=%s AND config_hash=%s
                    """,
                        (adapter.source, adapter.config_hash),
                    )
            if result is None:
                try:
                    derived = derive(adapter, page.html)
                    result = replay(derived.plan, page.html, page.url, adapter, timezone)
                    model = derived.model
                    connection.execute(
                        """
                        INSERT INTO ingest.extraction_plan
                            (source, config_hash, plan, model, prompt_version, derived_at)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(source, config_hash) DO UPDATE SET plan=EXCLUDED.plan,
                            model=EXCLUDED.model, derived_at=EXCLUDED.derived_at
                    """,
                        (
                            adapter.source,
                            adapter.config_hash,
                            Jsonb(derived.plan),
                            model,
                            adapter.prompt_version,
                            clock(),
                        ),
                    )
                except Exception as error:
                    failures += int(not isinstance(error, httpx.HTTPError))
                    failure = error
            connection.execute(
                """
                UPDATE ingest.site_page SET events=%s, next_url=%s, next_selector=%s,
                    extraction_model=%s, prompt_version=%s, cache_hit=%s,
                    validation_failures=validation_failures+%s, extraction_skips=%s
                WHERE page_fetch_id=%s
            """,
                (
                    Jsonb(result.events) if result else None,
                    result.next_url if result else None,
                    result.next_selector if result else None,
                    model,
                    adapter.prompt_version,
                    cache_hit,
                    failures,
                    Jsonb(result.skipped) if result else Jsonb([]),
                    page.id,
                ),
            )
            connection.execute(
                "UPDATE ingest.page_fetch SET error_message=%s WHERE id=%s",
                (
                    "extraction_model_unavailable"
                    if isinstance(failure, httpx.HTTPError)
                    else "extraction_validation_failed"
                    if failure
                    else None,
                    page.id,
                ),
            )
        if failure is not None:
            # Persist invalidation and diagnostics before raising; no bad plan survives rollback.
            raise ValueError("site extraction failed after derivation") from failure
        assert result is not None
        return result

    def staged_events(self, run_id: uuid.UUID, config_hash: str) -> list[dict[str, object]]:
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                """
                SELECT page.events, page.extraction_model, page.prompt_version,
                       page.category, page.config_hash
                FROM ingest.site_page page
                JOIN ingest.page_fetch pf ON pf.id=page.page_fetch_id
                WHERE pf.run_id=%s AND page.config_hash=%s AND page.events IS NOT NULL
                ORDER BY pf.crawl_target_id, pf.page_number
            """,
                (run_id, config_hash),
            ).fetchall()
        unique: dict[str, dict[str, object]] = {}
        for events, model, prompt_version, category, config_hash in rows:
            for event in cast(list[dict[str, object]], events):
                unique.setdefault(
                    str(event["source_event_id"]),
                    {
                        "_format": "site-v1",
                        "event": event,
                        "category": category,
                        "extraction_model": model,
                        "extraction_prompt_version": prompt_version,
                        "extraction_config_hash": config_hash,
                    },
                )
        return list(unique.values())
