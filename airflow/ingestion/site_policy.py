"""Database-backed review gate and shared pacing for scraped sources."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import urlsplit

import psycopg

from ingestion.clock import Clock
from ingestion.site_repository import SiteRepository


class SourcePolicy:
    def __init__(
        self, database_url: str, clock: Clock, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self.url = SiteRepository(database_url).url
        self.clock = clock
        self.sleep = sleep

    @contextmanager
    def request(self, source: str, url: str, timeout: float) -> Iterator[float]:
        # A session lock covers the actual request, including browser rendering, across
        # workers and markets. Committed reservations survive a killed worker.
        with psycopg.connect(self.url, autocommit=True) as connection:
            connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 81))", (source,))
            try:
                row = connection.execute(
                    """SELECT enabled, access_policy, min_request_interval_seconds, next_fetch_at
                    FROM ingest.source_adapter WHERE source=%s""",
                    (source,),
                ).fetchone()
                if not row or not row[0] or not row[1] or row[1].get("status") != "reviewed":
                    raise ValueError("source is disabled or has no completed access review")
                policy = row[1]
                if urlsplit(url)[:3] not in [
                    urlsplit(value)[:3] for value in policy.get("listing_urls", [])
                ]:
                    raise ValueError("URL is outside the reviewed source listing scope")
                interval = float(row[2])
                delay = max(0.0, (row[3] - self.clock()).total_seconds()) if row[3] else 0
                if delay:
                    self.sleep(delay)
                connection.execute(
                    "UPDATE ingest.source_adapter SET next_fetch_at=%s WHERE source=%s",
                    (self.clock() + timedelta(seconds=timeout + interval), source),
                )
                try:
                    yield interval
                finally:
                    connection.execute(
                        "UPDATE ingest.source_adapter SET next_fetch_at=%s WHERE source=%s",
                        (self.clock() + timedelta(seconds=interval), source),
                    )
            finally:
                connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 81))", (source,))
