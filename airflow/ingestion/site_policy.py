"""Database-backed review gate and shared pacing for scraped sources."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import psycopg

from ingestion.clock import Clock
from ingestion.site_models import http_url
from ingestion.site_repository import SiteRepository


@dataclass
class RequestPermit:
    interval: float
    listing_urls: tuple[str, ...] = ()
    retry_at: datetime | None = None

    def observe(self, status: int, retry_after: str | None, now: datetime) -> None:
        if status not in (429, 503) or not isinstance(retry_after, str) or not retry_after:
            return
        try:
            value = retry_after.strip()
            retry_at = (
                now + timedelta(seconds=int(value))
                if value.isascii() and value.isdecimal()
                else parsedate_to_datetime(value)
            )
            if retry_at.tzinfo is not None:
                self.retry_at = max(now, retry_at.astimezone(UTC))
        except (ValueError, TypeError, OverflowError):
            # Malformed server advice never cancels the source's configured interval.
            return

    def check_url(self, url: str) -> None:
        if self.listing_urls and urlsplit(http_url(url))[:3] not in [
            urlsplit(value)[:3] for value in self.listing_urls
        ]:
            raise ValueError("URL is outside the reviewed source listing scope")


class SourceDeferred(ValueError):
    """Persisted cooldown exceeds this task's bounded wait budget."""


def reviewed_permit(row: tuple[object, ...] | None) -> RequestPermit:
    if not row or not row[0] or not isinstance(row[1], dict):
        raise ValueError("source is disabled or has no completed access review")
    policy = row[1]
    if policy.get("status") != "reviewed":
        raise ValueError("source is disabled or has no completed access review")
    try:
        reviewed = datetime.fromisoformat(str(policy["reviewed_at"]).replace("Z", "+00:00"))
        if reviewed.tzinfo is None or not str(policy["notes"]).strip():
            raise ValueError("incomplete review")
        for key in ("listing_urls", "references"):
            values = policy[key]
            if not isinstance(values, list) or not values:
                raise ValueError("missing review URLs")
            for value in values:
                if not isinstance(value, str):
                    raise ValueError("invalid review URL")
                http_url(value)
        interval = float(str(row[2]))
        if not 1 <= interval <= 3600:
            raise ValueError("invalid interval")
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("source access review is malformed") from error
    return RequestPermit(interval, tuple(policy["listing_urls"]))


class SourcePolicy:
    def __init__(
        self, database_url: str, clock: Clock, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self.url = SiteRepository(database_url).url
        self.clock = clock
        self.sleep = sleep

    @contextmanager
    def request(self, source: str, url: str, timeout: float) -> Iterator[RequestPermit]:
        # A session lock covers the actual request, including browser rendering, across
        # workers and markets. Committed reservations survive a killed worker.
        with psycopg.connect(self.url, autocommit=True) as connection:
            connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 81))", (source,))
            try:
                while True:
                    row = connection.execute(
                        """SELECT enabled, access_policy, min_request_interval_seconds,
                                  next_fetch_at FROM ingest.source_adapter WHERE source=%s""",
                        (source,),
                    ).fetchone()
                    permit = reviewed_permit(row)
                    permit.check_url(url)
                    assert row is not None
                    delay = max(0.0, (row[3] - self.clock()).total_seconds()) if row[3] else 0
                    if delay > timeout:
                        raise SourceDeferred("source cooldown exceeds request wait budget")
                    if not delay:
                        break
                    self.sleep(delay)
                    # An operator may revoke review or change the interval while waiting.
                connection.execute(
                    "UPDATE ingest.source_adapter SET next_fetch_at=%s WHERE source=%s",
                    (self.clock() + timedelta(seconds=timeout + permit.interval), source),
                )
                try:
                    yield permit
                finally:
                    next_fetch = self.clock() + timedelta(seconds=permit.interval)
                    if permit.retry_at:
                        next_fetch = max(next_fetch, permit.retry_at)
                    connection.execute(
                        "UPDATE ingest.source_adapter SET next_fetch_at=%s WHERE source=%s",
                        (next_fetch, source),
                    )
            finally:
                connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 81))", (source,))
