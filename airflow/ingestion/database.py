"""Postgres persistence for ingestion runs and raw Eventbrite listings."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import cast

import psycopg
from psycopg.types.json import Jsonb

from ingestion.eventbrite import staging_identity
from ingestion.models import EventbriteListingPage, FetchedPage

RUN_NAMESPACE = uuid.UUID("09ab41b7-5cd1-4328-813c-f2becd62ce20")
LISTING_NAMESPACE = uuid.UUID("a276b217-3c85-42ea-81a7-a7b26ba6025e")
PAGE_FETCH_NAMESPACE = uuid.UUID("e3c5616e-0a4d-432c-a739-ebd9e4f71bc2")
REJECTION_NAMESPACE = uuid.UUID("a9404cd0-b503-488a-9f6b-c271bb68dba1")


class IngestionRepository:
    """Persist ingestion state using short, atomic Postgres transactions."""

    def __init__(self, database_url: str) -> None:
        self._database_url = _psycopg_url(database_url)

    def open_run(
        self,
        *,
        dag_id: str,
        airflow_run_id: str,
        source_url: str,
        city: str,
        started_at: datetime,
        categories: tuple[str, ...] = (),
        window_start: date | None = None,
        window_end: date | None = None,
    ) -> uuid.UUID:
        """Open or reset one deterministic source/location run for an Airflow execution."""
        run_id = uuid.uuid5(RUN_NAMESPACE, f"{dag_id}:{airflow_run_id}:eventbrite:{city}")
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.run (
                    id, run_date, source, source_url, city_searched, categories,
                    window_start, window_end, started_at, finished_at, status,
                    listing_appearances, events_found, detail_fetched, detail_cached,
                    events_new, events_updated, events_deduped, events_rejected_online,
                    error_message, airflow_dag_id, airflow_run_id
                ) VALUES (
                    %(id)s, %(run_date)s, 'eventbrite', %(source_url)s, %(city)s,
                    %(categories)s, %(window_start)s, %(window_end)s, %(started_at)s,
                    NULL, 'running', 0, 0, 0, 0, 0, 0, 0, 0, NULL,
                    %(dag_id)s, %(airflow_run_id)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    run_date = EXCLUDED.run_date,
                    source_url = EXCLUDED.source_url,
                    city_searched = EXCLUDED.city_searched,
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
                    "source_url": source_url,
                    "city": city,
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
        page: EventbriteListingPage,
        fetched_at: datetime,
    ) -> None:
        """Persist public listing-page metadata without staging listing-card payloads."""
        fetch_id = uuid.uuid5(PAGE_FETCH_NAMESPACE, f"{run_id}:{page.url}")
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.page_fetch (
                    id, run_id, url, search_target, page_number, http_status,
                    fetch_method, duration_ms, bytes, error_message, fetched_at
                ) VALUES (
                    %(id)s, %(run_id)s, %(url)s, %(search_target)s, %(page_number)s,
                    %(http_status)s, 'http', %(duration_ms)s, %(bytes)s, NULL,
                    %(fetched_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
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
                source_event_id, url = staging_identity(payload)
                listing_id = uuid.uuid5(LISTING_NAMESPACE, source_event_id)
                connection.execute(
                    """
                    INSERT INTO source_listing (
                        id, canonical_event_id, source, source_event_id, url,
                        registration_url, raw_payload, ingestion_run_id,
                        first_seen_at, last_seen_at
                    ) VALUES (
                        %(id)s, NULL, 'eventbrite', %(source_event_id)s, %(url)s,
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
        source_event_id, url = staging_identity(payload)
        listing_id = uuid.uuid5(LISTING_NAMESPACE, source_event_id)
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO source_listing (
                    id, canonical_event_id, source, source_event_id, url,
                    registration_url, raw_payload, ingestion_run_id,
                    first_seen_at, last_seen_at
                ) VALUES (
                    %(id)s, NULL, 'eventbrite', %(source_event_id)s, %(url)s,
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
                    "source_event_id": source_event_id,
                    "url": url,
                    "raw_payload": Jsonb(payload),
                    "run_id": run_id,
                    "seen_at": seen_at,
                },
            )

    def staged_payloads(self, run_id: uuid.UUID) -> tuple[dict[str, object], ...]:
        """Load the raw Eventbrite payloads staged by one run."""
        with psycopg.connect(self._database_url) as connection:
            rows = connection.execute(
                """
                SELECT raw_payload
                FROM source_listing
                WHERE source = 'eventbrite' AND ingestion_run_id = %(run_id)s
                ORDER BY source_event_id
                """,
                {"run_id": run_id},
            ).fetchall()
        payloads: list[dict[str, object]] = []
        for (raw_payload,) in rows:
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            if not isinstance(raw_payload, dict):
                raise TypeError("source_listing.raw_payload must contain a JSON object")
            payloads.append(cast(dict[str, object], raw_payload))
        return tuple(payloads)

    def reject_staged_listing(
        self,
        *,
        run_id: uuid.UUID,
        payload: dict[str, object],
        reason: str,
        rejected_at: datetime,
    ) -> None:
        """Log a rejection, removing new work or archiving an already-linked event."""
        source_event_id, url = staging_identity(payload)
        rejection_id = uuid.uuid5(
            REJECTION_NAMESPACE, f"{run_id}:eventbrite:{source_event_id}:{reason}"
        )
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO ingest.rejected_listing (
                    id, run_id, source, source_event_id, url, reason,
                    raw_payload, rejected_at
                ) VALUES (
                    %(id)s, %(run_id)s, 'eventbrite', %(source_event_id)s, %(url)s,
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
                WHERE source = 'eventbrite'
                  AND source_event_id = %(source_event_id)s
                  AND canonical_event_id IS NULL
                """,
                {"source_event_id": source_event_id},
            )
            if deleted.rowcount == 0:
                connection.execute(
                    """
                    UPDATE canonical_event AS event
                    SET archived_at = %(rejected_at)s,
                        updated_at = %(rejected_at)s
                    FROM source_listing AS listing
                    WHERE listing.source = 'eventbrite'
                      AND listing.source_event_id = %(source_event_id)s
                      AND listing.canonical_event_id = event.id
                    """,
                    {
                        "source_event_id": source_event_id,
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
