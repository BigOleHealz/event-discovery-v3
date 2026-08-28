"""Postgres persistence for ingestion runs and raw Eventbrite listings."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import cast

import psycopg
from psycopg.types.json import Jsonb

from ingestion.models import CrawlTarget, EventbriteListingPage, FetchedPage, MeetupListingPage
from ingestion.sources import source_identity

RUN_NAMESPACE = uuid.UUID("09ab41b7-5cd1-4328-813c-f2becd62ce20")
LISTING_NAMESPACE = uuid.UUID("a276b217-3c85-42ea-81a7-a7b26ba6025e")
PAGE_FETCH_NAMESPACE = uuid.UUID("e3c5616e-0a4d-432c-a739-ebd9e4f71bc2")
REJECTION_NAMESPACE = uuid.UUID("a9404cd0-b503-488a-9f6b-c271bb68dba1")


class IngestionRepository:
    """Persist ingestion state using short, atomic Postgres transactions."""

    def __init__(self, database_url: str) -> None:
        self._database_url = _psycopg_url(database_url)

    def enabled_crawl_targets(self, *, source: str) -> tuple[CrawlTarget, ...]:
        """Load the authoritative enabled target inventory for one source."""
        with psycopg.connect(self._database_url) as connection:
            rows = connection.execute(
                """
                SELECT target.id, target.source, target.market_id, market.slug,
                       market.name, market.timezone, target.source_location, target.category,
                       target.window_days, target.page_cap
                FROM ingest.crawl_target AS target
                JOIN ingest.market AS market ON market.id = target.market_id
                WHERE target.source = %(source)s AND target.enabled
                ORDER BY market.slug, target.category, target.id
                """,
                {"source": source},
            ).fetchall()
        targets: list[CrawlTarget] = []
        for row in rows:
            (
                target_id,
                row_source,
                market_id,
                market_slug,
                market_name,
                market_timezone,
                source_location,
                category,
                window_days,
                page_cap,
            ) = row
            if not isinstance(target_id, uuid.UUID) or not isinstance(market_id, uuid.UUID):
                raise TypeError("crawl_target.id and market_id must be UUIDs")
            if not all(
                isinstance(value, str)
                for value in (row_source, market_slug, market_name, market_timezone, category)
            ):
                raise TypeError("crawl target source, market, and category must be text")
            if not isinstance(source_location, dict):
                raise TypeError("crawl_target.source_location must be a JSON object")
            if not isinstance(window_days, int) or not isinstance(page_cap, int):
                raise TypeError("crawl_target window_days and page_cap must be integers")
            targets.append(
                CrawlTarget(
                    id=target_id,
                    source=row_source,
                    market_id=market_id,
                    market_slug=market_slug,
                    market_name=market_name,
                    market_timezone=market_timezone,
                    source_location=source_location,
                    category=category,
                    window_days=window_days,
                    page_cap=page_cap,
                )
            )
        return tuple(targets)

    def open_run(
        self,
        *,
        dag_id: str,
        airflow_run_id: str,
        source: str,
        source_url: str,
        market_id: uuid.UUID,
        started_at: datetime,
        categories: tuple[str, ...] = (),
        window_start: date | None = None,
        window_end: date | None = None,
    ) -> uuid.UUID:
        """Open or reset one deterministic source/market run for an Airflow execution."""
        run_id = uuid.uuid5(RUN_NAMESPACE, f"{dag_id}:{airflow_run_id}:{source}:{market_id}")
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.run (
                    id, run_date, source, source_url, market_id, categories,
                    window_start, window_end, started_at, finished_at, status,
                    listing_appearances, events_found, detail_fetched, detail_cached,
                    events_new, events_updated, events_deduped, events_rejected_online,
                    error_message, airflow_dag_id, airflow_run_id
                ) VALUES (
                    %(id)s, %(run_date)s, %(source)s, %(source_url)s,
                    %(market_id)s, %(categories)s, %(window_start)s, %(window_end)s, %(started_at)s,
                    NULL, 'running', 0, 0, 0, 0, 0, 0, 0, 0, NULL,
                    %(dag_id)s, %(airflow_run_id)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    run_date = EXCLUDED.run_date,
                    source_url = EXCLUDED.source_url,
                    market_id = EXCLUDED.market_id,
                    categories = EXCLUDED.categories,
                    window_start = EXCLUDED.window_start,
                    window_end = EXCLUDED.window_end,
                    started_at = EXCLUDED.started_at,
                    finished_at = NULL,
                    status = 'running',
                    listing_appearances = 0,
                    events_found = 0,
                    detail_fetched = 0,
                    detail_cached = 0,
                    events_new = 0,
                    events_updated = 0,
                    events_deduped = 0,
                    events_rejected_online = 0,
                    error_message = NULL
                """,
                {
                    "id": run_id,
                    "run_date": started_at.date(),
                    "source": source,
                    "source_url": source_url,
                    "market_id": market_id,
                    "categories": list(categories),
                    "window_start": window_start,
                    "window_end": window_end,
                    "started_at": started_at,
                    "dag_id": dag_id,
                    "airflow_run_id": airflow_run_id,
                },
            )
        return run_id

    def record_page_fetch(
        self,
        *,
        run_id: uuid.UUID,
        page: EventbriteListingPage | MeetupListingPage,
        fetched_at: datetime,
    ) -> None:
        """Persist public listing-page metadata without staging listing-card payloads."""
        fetch_id = uuid.uuid5(
            PAGE_FETCH_NAMESPACE,
            (
                f"{run_id}:{page.crawl_target_id}:{page.search_target}:"
                f"{page.page_number}:{page.url}"
            ),
        )
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.page_fetch (
                    id, run_id, crawl_target_id, url, search_target, page_number,
                    http_status, fetch_method, duration_ms, bytes, error_message, fetched_at
                ) VALUES (
                    %(id)s, %(run_id)s, %(crawl_target_id)s, %(url)s,
                    %(search_target)s, %(page_number)s, %(http_status)s, 'http',
                    %(duration_ms)s, %(bytes)s, NULL, %(fetched_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    crawl_target_id = EXCLUDED.crawl_target_id,
                    search_target = EXCLUDED.search_target,
                    page_number = EXCLUDED.page_number,
                    http_status = EXCLUDED.http_status,
                    duration_ms = EXCLUDED.duration_ms,
                    bytes = EXCLUDED.bytes,
                    error_message = NULL,
                    fetched_at = EXCLUDED.fetched_at
                """,
                {
                    "id": fetch_id,
                    "run_id": run_id,
                    "crawl_target_id": page.crawl_target_id,
                    "url": page.url,
                    "search_target": page.search_target,
                    "page_number": page.page_number,
                    "http_status": page.http_status,
                    "duration_ms": page.duration_ms,
                    "bytes": page.byte_count,
                    "fetched_at": fetched_at,
                },
            )

    def record_and_stage_page(
        self,
        *,
        run_id: uuid.UUID,
        page: FetchedPage,
        seen_at: datetime,
        source: str = "eventbrite",
    ) -> int:
        """Atomically record fetch metadata and stage every raw event from the page."""
        fetch_id = uuid.uuid5(PAGE_FETCH_NAMESPACE, f"{run_id}:{page.url}")
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.page_fetch (
                    id, run_id, url, http_status, fetch_method, duration_ms,
                    bytes, error_message, fetched_at
                ) VALUES (
                    %(id)s, %(run_id)s, %(url)s, %(http_status)s, 'http',
                    %(duration_ms)s, %(bytes)s, NULL, %(fetched_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    http_status = EXCLUDED.http_status,
                    duration_ms = EXCLUDED.duration_ms,
                    bytes = EXCLUDED.bytes,
                    error_message = NULL,
                    fetched_at = EXCLUDED.fetched_at
                """,
                {
                    "id": fetch_id,
                    "run_id": run_id,
                    "url": page.url,
                    "http_status": page.http_status,
                    "duration_ms": page.duration_ms,
                    "bytes": page.byte_count,
                    "fetched_at": seen_at,
                },
            )
            for payload in page.events:
                source_event_id, url = source_identity(source, payload)
                listing_id = uuid.uuid5(LISTING_NAMESPACE, f"{source}:{source_event_id}")
                connection.execute(
                    """
                    INSERT INTO source_listing (
                        id, canonical_event_id, source, source_event_id, url,
                        registration_url, raw_payload, ingestion_run_id,
                        first_seen_at, last_seen_at
                    ) VALUES (
                        %(id)s, NULL, %(source)s, %(source_event_id)s, %(url)s,
                        %(url)s, %(raw_payload)s, %(run_id)s, %(seen_at)s, %(seen_at)s
                    )
                    ON CONFLICT (source, source_event_id) DO UPDATE SET
                        url = EXCLUDED.url,
                        registration_url = EXCLUDED.registration_url,
                        raw_payload = EXCLUDED.raw_payload,
                        ingestion_run_id = EXCLUDED.ingestion_run_id,
                        last_seen_at = EXCLUDED.last_seen_at
                    """,
                    {
                        "id": listing_id,
                        "source": source,
                        "source_event_id": source_event_id,
                        "url": url,
                        "raw_payload": Jsonb(payload),
                        "run_id": run_id,
                        "seen_at": seen_at,
                    },
                )
        return len(page.events)

    def cached_event_detail(
        self, *, source_event_id: str, checked_at: datetime
    ) -> dict[str, object] | None:
        """Return an unexpired Eventbrite detail payload, if one is cached."""
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT raw_payload
                FROM ingest.event_detail_cache
                WHERE source = 'eventbrite'
                  AND source_event_id = %(source_event_id)s
                  AND expires_at > %(checked_at)s
                """,
                {"source_event_id": source_event_id, "checked_at": checked_at},
            ).fetchone()
        if row is None:
            return None
        raw_payload = row[0]
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        if not isinstance(raw_payload, dict):
            raise TypeError("event detail cache must contain a JSON object")
        return cast(dict[str, object], raw_payload)

    def cache_event_detail(
        self,
        *,
        source_event_id: str,
        payload: dict[str, object],
        fetched_at: datetime,
        ttl: timedelta,
    ) -> None:
        """Upsert a detail payload with an explicit expiration timestamp."""
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.event_detail_cache (
                    source, source_event_id, raw_payload, fetched_at, expires_at
                ) VALUES (
                    'eventbrite', %(source_event_id)s, %(raw_payload)s,
                    %(fetched_at)s, %(expires_at)s
                )
                ON CONFLICT (source, source_event_id) DO UPDATE SET
                    raw_payload = EXCLUDED.raw_payload,
                    fetched_at = EXCLUDED.fetched_at,
                    expires_at = EXCLUDED.expires_at
                """,
                {
                    "source_event_id": source_event_id,
                    "raw_payload": Jsonb(payload),
                    "fetched_at": fetched_at,
                    "expires_at": fetched_at + ttl,
                },
            )

    def stage_event_detail(
        self,
        *,
        run_id: uuid.UUID,
        payload: dict[str, object],
        seen_at: datetime,
    ) -> None:
        """Stage one full detail payload after exact crawl-level id deduplication."""
        self.stage_source_payload(
            source="eventbrite", run_id=run_id, payload=payload, seen_at=seen_at
        )

    def stage_source_payload(
        self,
        *,
        source: str,
        run_id: uuid.UUID,
        payload: dict[str, object],
        seen_at: datetime,
    ) -> None:
        """Stage one source payload after exact crawl-level id deduplication."""
        source_event_id, url = source_identity(source, payload)
        listing_id = uuid.uuid5(LISTING_NAMESPACE, f"{source}:{source_event_id}")
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO source_listing (
                    id, canonical_event_id, source, source_event_id, url,
                    registration_url, raw_payload, ingestion_run_id,
                    first_seen_at, last_seen_at
                ) VALUES (
                    %(id)s, NULL, %(source)s, %(source_event_id)s, %(url)s,
                    %(url)s, %(raw_payload)s, %(run_id)s, %(seen_at)s, %(seen_at)s
                )
                ON CONFLICT (source, source_event_id) DO UPDATE SET
                    url = EXCLUDED.url,
                    registration_url = EXCLUDED.registration_url,
                    raw_payload = EXCLUDED.raw_payload,
                    ingestion_run_id = EXCLUDED.ingestion_run_id,
                    last_seen_at = EXCLUDED.last_seen_at
                """,
                {
                    "id": listing_id,
                    "source": source,
                    "source_event_id": source_event_id,
                    "url": url,
                    "raw_payload": Jsonb(payload),
                    "run_id": run_id,
                    "seen_at": seen_at,
                },
            )

    def staged_payloads(
        self, run_id: uuid.UUID, *, source: str = "eventbrite"
    ) -> tuple[dict[str, object], ...]:
        """Load raw payloads staged by one source run."""
        with psycopg.connect(self._database_url) as connection:
            rows = connection.execute(
                """
                SELECT raw_payload
                FROM source_listing
                WHERE source = %(source)s AND ingestion_run_id = %(run_id)s
                ORDER BY source_event_id
                """,
                {"run_id": run_id, "source": source},
            ).fetchall()
        payloads: list[dict[str, object]] = []
        for (raw_payload,) in rows:
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            if not isinstance(raw_payload, dict):
                raise TypeError("source_listing.raw_payload must contain a JSON object")
            payloads.append(cast(dict[str, object], raw_payload))
        return tuple(payloads)

    def run_market_timezone(self, run_id: uuid.UUID) -> str:
        """Return the canonical market timezone attached to a source run."""
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT market.timezone
                FROM ingest.run AS run
                JOIN ingest.market AS market ON market.id = run.market_id
                WHERE run.id = %(run_id)s
                """,
                {"run_id": run_id},
            ).fetchone()
        if row is None or not isinstance(row[0], str):
            raise LookupError(f"ingest.run {run_id} has no canonical market timezone")
        return row[0]

    def reject_staged_listing(
        self,
        *,
        run_id: uuid.UUID,
        payload: dict[str, object],
        reason: str,
        rejected_at: datetime,
        source: str = "eventbrite",
    ) -> None:
        """Log a rejection, removing new work or archiving an already-linked event."""
        source_event_id, url = source_identity(source, payload)
        rejection_id = uuid.uuid5(
            REJECTION_NAMESPACE, f"{run_id}:{source}:{source_event_id}:{reason}"
        )
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.rejected_listing (
                    id, run_id, source, source_event_id, url, reason,
                    raw_payload, rejected_at
                ) VALUES (
                    %(id)s, %(run_id)s, %(source)s, %(source_event_id)s, %(url)s,
                    %(reason)s, %(raw_payload)s, %(rejected_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    url = EXCLUDED.url,
                    reason = EXCLUDED.reason,
                    raw_payload = EXCLUDED.raw_payload,
                    rejected_at = EXCLUDED.rejected_at
                """,
                {
                    "id": rejection_id,
                    "run_id": run_id,
                    "source": source,
                    "source_event_id": source_event_id,
                    "url": url,
                    "reason": reason,
                    "raw_payload": Jsonb(payload),
                    "rejected_at": rejected_at,
                },
            )
            deleted = connection.execute(
                """
                DELETE FROM source_listing
                WHERE source = %(source)s
                  AND source_event_id = %(source_event_id)s
                  AND canonical_event_id IS NULL
                """,
                {"source": source, "source_event_id": source_event_id},
            )
            if deleted.rowcount == 0:
                connection.execute(
                    """
                    UPDATE canonical_event AS event
                    SET archived_at = %(rejected_at)s,
                        updated_at = %(rejected_at)s
                    FROM source_listing AS listing
                    WHERE listing.source = %(source)s
                      AND listing.source_event_id = %(source_event_id)s
                      AND listing.canonical_event_id = event.id
                    """,
                    {
                        "source_event_id": source_event_id,
                        "source": source,
                        "rejected_at": rejected_at,
                    },
                )

    def mark_success(
        self,
        *,
        run_id: uuid.UUID,
        events_found: int,
        finished_at: datetime,
        events_rejected_online: int = 0,
        listing_appearances: int = 0,
        detail_fetched: int = 0,
        detail_cached: int = 0,
    ) -> None:
        """Close a run successfully."""
        self._update_run(
            run_id=run_id,
            status="success",
            events_found=events_found,
            error_message=None,
            finished_at=finished_at,
            events_rejected_online=events_rejected_online,
            listing_appearances=listing_appearances,
            detail_fetched=detail_fetched,
            detail_cached=detail_cached,
        )

    def mark_partial(
        self,
        *,
        run_id: uuid.UUID,
        events_found: int,
        listing_appearances: int,
        detail_fetched: int,
        detail_cached: int,
        events_rejected_online: int,
        reason: str,
        finished_at: datetime,
    ) -> None:
        """Close a rate-limited run while preserving its known ids and staged details."""
        self._update_run(
            run_id=run_id,
            status="partial",
            events_found=events_found,
            error_message=reason[:4000],
            finished_at=finished_at,
            events_rejected_online=events_rejected_online,
            listing_appearances=listing_appearances,
            detail_fetched=detail_fetched,
            detail_cached=detail_cached,
        )

    def mark_failed(self, *, run_id: uuid.UUID, error: Exception, finished_at: datetime) -> None:
        """Close a run as failed while preserving staged raw payloads."""
        self._update_run(
            run_id=run_id,
            status="failed",
            events_found=None,
            error_message=str(error)[:4000],
            finished_at=finished_at,
            events_rejected_online=None,
            listing_appearances=None,
            detail_fetched=None,
            detail_cached=None,
        )

    def _update_run(
        self,
        *,
        run_id: uuid.UUID,
        status: str,
        events_found: int | None,
        error_message: str | None,
        finished_at: datetime,
        events_rejected_online: int | None,
        listing_appearances: int | None,
        detail_fetched: int | None,
        detail_cached: int | None,
    ) -> None:
        with psycopg.connect(self._database_url) as connection:
            cursor = connection.execute(
                """
                UPDATE ingest.run
                SET status = %(status)s,
                    finished_at = %(finished_at)s,
                    error_message = %(error_message)s,
                    events_found = COALESCE(%(events_found)s, events_found),
                    listing_appearances = COALESCE(
                        %(listing_appearances)s, listing_appearances
                    ),
                    detail_fetched = COALESCE(%(detail_fetched)s, detail_fetched),
                    detail_cached = COALESCE(%(detail_cached)s, detail_cached),
                    events_rejected_online = COALESCE(
                        %(events_rejected_online)s, events_rejected_online
                    )
                WHERE id = %(run_id)s
                """,
                {
                    "run_id": run_id,
                    "status": status,
                    "error_message": error_message,
                    "finished_at": finished_at,
                    "events_found": events_found,
                    "listing_appearances": listing_appearances,
                    "detail_fetched": detail_fetched,
                    "detail_cached": detail_cached,
                    "events_rejected_online": events_rejected_online,
                },
            )
            if cursor.rowcount != 1:
                raise LookupError(f"ingest.run {run_id} does not exist")


def _psycopg_url(database_url: str) -> str:
    """Accept the SQLAlchemy psycopg URL already shared by Compose."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)
