from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email.utils import format_datetime

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_local_sites import FakeClock
from test_sites import CONFIG, adapter, seed

from ingestion.site_pipeline import SiteClient
from ingestion.site_policy import RequestPermit, SourceDeferred, SourcePolicy


@pytest.fixture
def policy(database_url: str, clean_ingestion_tables: None) -> SourcePolicy:
    seed(database_url)
    clock = FakeClock()
    return SourcePolicy(database_url, lambda: clock.now, clock.sleep)


@pytest.mark.parametrize("method", ["http", "stagehand"])
@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("date_header", [False, True])
def test_server_backoff_survives_new_worker(
    policy: SourcePolicy, method: str, status: int, date_header: bool
) -> None:
    retry_at = policy.clock() + timedelta(seconds=60)
    value = format_datetime(retry_at, usegmt=True) if date_header else "60"

    def service(request: httpx.Request) -> httpx.Response:
        if method == "http":
            return httpx.Response(status, text="retry later", headers={"Retry-After": value})
        return httpx.Response(
            200,
            json={
                "url": "https://fixture.test/events",
                "http_status": status,
                "html": "retry later",
                "retry_after": value,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(service)) as http:
        result = SiteClient(http, CONFIG, policy).fetch(
            adapter(method), "https://fixture.test/events", []
        )
        assert result[1] == status
    with psycopg.connect(policy.url) as connection:
        assert connection.execute(
            "SELECT next_fetch_at FROM ingest.source_adapter WHERE source='fixture-site'"
        ).fetchone() == (retry_at,)
    other = SourcePolicy(policy.url, policy.clock, policy.sleep)
    with other.request("fixture-site", "https://fixture.test/events", 100):
        assert other.clock() == retry_at


@pytest.mark.parametrize("header", [None, "garbage", "-5", "NaN", "9" * 500])
def test_invalid_backoff_preserves_minimum(policy: SourcePolicy, header: str | None) -> None:
    before = policy.clock()
    with policy.request("fixture-site", "https://fixture.test/events", 100) as permit:
        permit.observe(429, header, policy.clock())
    with policy.request("fixture-site", "https://fixture.test/events", 100):
        assert policy.clock() == before + timedelta(seconds=1)


def test_long_cooldown_defers_without_waiting_or_shortening_it(policy: SourcePolicy) -> None:
    retry_at = policy.clock() + timedelta(days=1)
    with policy.request("fixture-site", "https://fixture.test/events", 100) as permit:
        permit.observe(503, "86400", policy.clock())
    before = policy.clock()
    with (
        pytest.raises(SourceDeferred),
        policy.request("fixture-site", "https://fixture.test/events", 100),
    ):
        pytest.fail("source fetched during cooldown")
    assert policy.clock() == before
    with psycopg.connect(policy.url) as connection:
        assert connection.execute(
            "SELECT next_fetch_at FROM ingest.source_adapter WHERE source='fixture-site'"
        ).fetchone() == (retry_at,)


def test_revocation_during_wait_prevents_fetch(policy: SourcePolicy) -> None:
    with policy.request("fixture-site", "https://fixture.test/events", 100):
        pass
    sleep = policy.sleep

    def revoke(seconds: float) -> None:
        sleep(seconds)
        with psycopg.connect(policy.url) as connection:
            connection.execute(
                "UPDATE ingest.source_adapter SET enabled=false WHERE source='fixture-site'"
            )

    policy.sleep = revoke
    with (
        pytest.raises(ValueError, match="disabled"),
        policy.request("fixture-site", "https://fixture.test/events", 100),
    ):
        pytest.fail("revoked source fetched")


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewed_at", "not-a-date"),
        ("reviewed_at", "2026-09-27"),
        ("listing_urls", [None]),
        ("references", ["not-a-url"]),
    ],
)
def test_malformed_review_fails_closed(policy: SourcePolicy, field: str, value: object) -> None:
    with psycopg.connect(policy.url) as connection:
        connection.execute(
            "UPDATE ingest.source_adapter SET access_policy="
            "jsonb_set(access_policy,%s,%s) WHERE source='fixture-site'",
            ([field], Jsonb(value)),
        )
    with (
        pytest.raises(ValueError, match="malformed"),
        policy.request("fixture-site", "https://fixture.test/events", 100),
    ):
        pytest.fail("malformed policy accepted")


def test_competing_workers_share_lock_and_interval(policy: SourcePolicy) -> None:
    queued = threading.Event()
    admitted = threading.Event()
    before = policy.clock()

    def worker() -> None:
        other = SourcePolicy(policy.url, policy.clock, policy.sleep)
        queued.set()
        with other.request("fixture-site", "https://fixture.test/events", 100):
            admitted.set()
            assert other.clock() == before + timedelta(seconds=1)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with policy.request("fixture-site", "https://fixture.test/events", 100):
            future = pool.submit(worker)
            assert queued.wait(5)
            # Observe a genuinely waiting database lock, not an arbitrary thread sleep.
            with psycopg.connect(policy.url) as connection:
                for _ in range(100):
                    waiting = connection.execute(
                        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted"
                    ).fetchone()
                    if waiting == (1,):
                        break
                assert waiting == (1,)
            assert not admitted.is_set()
        future.result(timeout=5)
    assert admitted.is_set()


def test_http_redirect_checks_review_before_next_request(policy: SourcePolicy) -> None:
    calls: list[str] = []

    def service(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "/unreviewed"})

    with httpx.Client(transport=httpx.MockTransport(service)) as http:
        with pytest.raises(ValueError, match="outside the reviewed"):
            SiteClient(http, CONFIG, policy).fetch(adapter(), "https://fixture.test/events", [])
    assert calls == ["https://fixture.test/events"]


def test_browser_result_cannot_escape_reviewed_path(policy: SourcePolicy) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "url": "https://fixture.test/unreviewed",
                    "http_status": 200,
                    "html": "outside scope",
                },
            )
        )
    ) as http:
        with pytest.raises(ValueError, match="outside the reviewed"):
            SiteClient(http, CONFIG, policy).fetch(
                adapter("stagehand"), "https://fixture.test/events", []
            )


def test_non_error_response_does_not_set_backoff() -> None:
    permit = RequestPermit(10)
    permit.observe(200, "1000", FakeClock().now)
    assert permit.retry_at is None


def test_killed_worker_keeps_committed_reservation(policy: SourcePolicy) -> None:
    before = policy.clock()
    with pytest.raises(psycopg.OperationalError):
        with policy.request("fixture-site", "https://fixture.test/events", 100):
            with psycopg.connect(policy.url, autocommit=True) as observer:
                pid = observer.execute(
                    "SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted"
                ).fetchone()[0]
                observer.execute("SELECT pg_terminate_backend(%s)", (pid,))
    with psycopg.connect(policy.url) as connection:
        assert connection.execute(
            "SELECT next_fetch_at FROM ingest.source_adapter WHERE source='fixture-site'"
        ).fetchone() == (before + timedelta(seconds=101),)
    with (
        pytest.raises(SourceDeferred),
        policy.request("fixture-site", "https://fixture.test/events", 100),
    ):
        pytest.fail("crashed worker's reservation ignored")


def test_browser_service_backoff_persists_on_http_exception(policy: SourcePolicy) -> None:
    before = policy.clock()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503, headers={"Retry-After": "300"}, json={"error": "browser_busy"}
            )
        )
    ) as http:
        with pytest.raises(httpx.HTTPStatusError):
            SiteClient(http, CONFIG, policy).fetch(
                adapter("stagehand"), "https://fixture.test/events", []
            )
    with psycopg.connect(policy.url) as connection:
        assert connection.execute(
            "SELECT next_fetch_at FROM ingest.source_adapter WHERE source='fixture-site'"
        ).fetchone() == (before + timedelta(seconds=300),)
