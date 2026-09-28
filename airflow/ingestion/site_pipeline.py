"""Generic site fetching and bounded pagination, shared by HTTP and Stagehand DAGs."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from ingestion.clock import Clock
from ingestion.models import CrawlTarget
from ingestion.site_extraction import DerivePlan
from ingestion.site_models import SiteAdapter, http_url
from ingestion.site_policy import RequestPermit, SourcePolicy
from ingestion.site_repository import PAGE_NAMESPACE, SitePage, SiteRepository


@dataclass(frozen=True)
class SiteProcessConfig:
    stagehand_url: str
    stagehand_token: str
    extraction_api_url: str
    extraction_api_key: str
    timeout_seconds: float = 100
    max_html_bytes: int = 5_000_000
    max_model_input_bytes: int = 250_000

    @classmethod
    def from_env(cls) -> SiteProcessConfig:
        timeout = float(os.environ.get("SITE_REQUEST_TIMEOUT_SECONDS", "100"))
        limit = int(os.environ.get("SITE_MAX_HTML_BYTES", "5000000"))
        model_limit = int(os.environ.get("EXTRACTION_MAX_INPUT_BYTES", "250000"))
        if (
            not 0 < timeout <= 600
            or not 0 < limit <= 20_000_000
            or not 0 < model_limit <= 5_000_000
        ):
            raise ValueError("invalid site request timeout or size limit")
        return cls(
            os.environ.get("STAGEHAND_BASE_URL", ""),
            os.environ.get("STAGEHAND_API_TOKEN", ""),
            os.environ.get("EXTRACTION_API_URL", ""),
            os.environ.get("EXTRACTION_API_KEY", ""),
            timeout,
            limit,
            model_limit,
        )


class SiteClient:
    def __init__(
        self,
        client: httpx.Client,
        config: SiteProcessConfig,
        policy: SourcePolicy | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.policy = policy

    def fetch(
        self,
        adapter: SiteAdapter,
        url: str,
        steps: list[dict[str, object]],
    ) -> tuple[str, int, str]:
        http_url(url)
        if adapter.fetch_method == "stagehand":
            if not self.config.stagehand_url or not self.config.stagehand_token:
                raise ValueError("Stagehand URL and token are required")
            body: dict[str, object] = {
                "url": url,
                "steps": steps,
            }
            if adapter.extraction.get("ready_selector"):
                body["ready_selector"] = adapter.extraction["ready_selector"]
            guard = (
                self.policy.request(adapter.source, url, self.config.timeout_seconds)
                if self.policy
                else nullcontext(RequestPermit(0.0))
            )
            with guard as permit:
                body["steps"] = [
                    {
                        **step,
                        "delay_ms": max(int(permit.interval * 1000), int(str(step["delay_ms"]))),
                    }
                    for step in steps
                ]
                response = self.client.post(
                    self.config.stagehand_url.rstrip("/") + "/v1/fetch",
                    json=body,
                    headers={"Authorization": f"Bearer {self.config.stagehand_token}"},
                )
                if self.policy:
                    permit.observe(
                        response.status_code,
                        response.headers.get("Retry-After"),
                        self.policy.clock(),
                    )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data.get("html"), str) or not isinstance(
                    data.get("http_status"), int
                ):
                    raise ValueError("Stagehand returned an invalid page response")
                if self.policy:
                    permit.observe(
                        data["http_status"], data.get("retry_after"), self.policy.clock()
                    )
                permit.check_url(data["url"])
                result = (http_url(data["url"]), data["http_status"], data["html"])
        else:
            current = url
            for _ in range(6):
                guard = (
                    self.policy.request(adapter.source, current, self.config.timeout_seconds)
                    if self.policy
                    else nullcontext(RequestPermit(0.0))
                )
                with (
                    guard as permit,
                    self.client.stream("GET", current, follow_redirects=False) as response,
                ):
                    if self.policy:
                        permit.observe(
                            response.status_code,
                            response.headers.get("Retry-After"),
                            self.policy.clock(),
                        )
                    if response.is_redirect:
                        current = same_origin(url, urljoin(current, response.headers["location"]))
                        continue
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self.config.max_html_bytes:
                            raise ValueError("site page exceeds size limit")
                    result = (
                        current,
                        response.status_code,
                        bytes(content).decode(response.encoding or "utf-8", errors="replace"),
                    )
                    break
            else:
                raise ValueError("too many source redirects")
        same_origin(url, result[0])
        if len(result[2].encode()) > self.config.max_html_bytes:
            raise ValueError("site page exceeds size limit")
        return result


def same_origin(base: str, value: str) -> str:
    http_url(value)
    if urlsplit(base)[:2] != urlsplit(value)[:2]:
        raise ValueError("pagination or redirect left the configured source origin")
    return value


@dataclass(frozen=True)
class SiteCrawlSummary:
    pages: int
    appearances: int
    partial_reason: str | None


def crawl_site(
    *,
    adapter: SiteAdapter,
    targets: Sequence[CrawlTarget],
    run_id: uuid.UUID,
    repository: SiteRepository,
    client: SiteClient,
    derive: DerivePlan,
    clock: Clock,
) -> SiteCrawlSummary:
    pages = appearances = 0
    partial_reason: str | None = None
    for target in targets:
        if target.source_location.get("kind") != "listing_url":
            raise ValueError("site target must use the listing_url location shape")
        base = http_url(str(target.source_location["url"]))
        url = base
        steps: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        previous_ids: set[str] | None = None
        for number in range(1, target.page_cap + 1):
            if adapter.pagination["kind"] == "query":
                parts = urlsplit(base)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                query[str(adapter.pagination["parameter"])] = str(number)
                url = urlunsplit(parts._replace(query=urlencode(query)))
            if adapter.pagination["kind"] != "action" and url in seen_urls:
                partial_reason = "pagination URL cycle"
                break
            seen_urls.add(url)
            page_id = uuid.uuid5(
                PAGE_NAMESPACE, f"{run_id}:{target.id}:{adapter.config_hash}:{number}"
            )
            page = repository.page(page_id, adapter.config_hash)
            if page is None:
                started = time.monotonic()
                try:
                    final_url, status, html = client.fetch(adapter, url, steps)
                except Exception:
                    # Failed transport attempts must also be visible in page_fetch.
                    repository.save_page(
                        page=SitePage(page_id, url, "", 599),
                        run_id=run_id,
                        target_id=target.id,
                        number=number,
                        adapter=adapter,
                        category=target.category,
                        fetched_at=clock(),
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    raise
                page = SitePage(page_id, final_url, html, status)
                repository.save_page(
                    page=page,
                    run_id=run_id,
                    target_id=target.id,
                    number=number,
                    adapter=adapter,
                    fetched_at=clock(),
                    category=target.category,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            pages += 1
            if page.status >= 400:
                partial_reason = f"source HTTP {page.status}"
                break
            extracted = repository.extract(page, adapter, target.market_timezone, derive, clock)
            appearances += len(extracted.events)
            ids = {str(event["source_event_id"]) for event in extracted.events}
            if (not ids and not extracted.skipped) or (ids and ids == previous_ids):
                break
            previous_ids = ids
            kind = adapter.pagination["kind"]
            if kind == "none":
                break
            if kind in ("next_link", "action") and not extracted.next_selector:
                break
            if kind == "next_link":
                assert extracted.next_url is not None
                url = same_origin(base, extracted.next_url)
            if kind == "action":
                steps.append(
                    {
                        "selector": extracted.next_selector,
                        "wait_for_selector": str(adapter.pagination["wait_for"]).replace(
                            "{page}", str(number + 1)
                        ),
                        "delay_ms": 1,
                    }
                )
                if len(steps) > 20:
                    partial_reason = "browser action replay cap reached"
                    break
        else:
            partial_reason = "page cap reached"
    return SiteCrawlSummary(pages, appearances, partial_reason)
