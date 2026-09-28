# Source access controls (Phase 8d)

Scraped-source inventory and review decisions live in `ingest.source_adapter`, with
enabled market/listing targets in `ingest.crawl_target`. Credentials, service endpoints
and DAG schedules remain environment variables. These controls apply to the shared HTTP
and Stagehand ingestion paths; documented API adapters keep their existing controls.

## Before enabling a source

Review the actual listing pages, linked terms and policies, and robots directives. Record
the review time, evidence URLs, decision and scope in `access_policy`. Review each source
separately even when multiple sites use the same platform. A reachable public page or
robots allowance alone does not establish permission. If terms prohibit the proposed use
or the review is unresolved, keep the source disabled and record `blocked` or `pending`.

The initial local-source reviews and bounded extraction scopes are in
[local-sources.md](local-sources.md). Future onboarding migrations must provide:

- `status`: `reviewed`, `pending` or `blocked`;
- `reviewed_at`: an ISO timestamp with a timezone;
- `listing_urls`: nonempty HTTP(S) listing URLs in the reviewed scope;
- `references`: nonempty HTTP(S) evidence URLs;
- `notes`: review findings, relevant restrictions and permitted operational scope;
- `min_request_interval_seconds`: a source-specific value from 1 through 3600 seconds.

The database rejects enabling an HTTP/Stagehand adapter without a reviewed policy and
interval. Runtime checks also reject malformed review dates and URLs. Migration
`20260928_0015` does not silently change existing inventory: any custom enabled scraper
without a review must be reviewed or disabled before that migration can succeed.

For a new source, insert it disabled, record the review and rate limit, then enable it in
the same onboarding migration only when the review is complete. Re-review before expanding
listing paths or extraction scope, or when terms change. Review status is an operational
decision, not a representation of a content license.

To block a source, update its decision and enablement atomically:

```sql
UPDATE ingest.source_adapter
SET enabled = false,
    access_policy = jsonb_set(access_policy, '{status}', '"blocked"')
WHERE source = :source;
```

Disabling alone is also supported. A worker rechecks the current review, allowed URL and
interval after a pacing wait, so a revocation during that wait prevents the fetch. This
does not cancel a request already in flight or erase previously ingested facts.

## Pacing and server backoff

Each source has a Postgres advisory lock shared across workers, processes and markets.
The lock covers fetching and rendering. `next_fetch_at` records a reservation before the
request, and completion moves it to at least the configured interval after completion,
including transport failures. If a worker dies, the committed reservation survives until
the request timeout plus interval. Other sources use independent locks.

HTTP redirects each acquire the gate before another request. Query parameters may vary
within a reviewed origin/path. Browser rendering is one guarded request, with pagination
clicks spaced by the interval; browser-internal redirects and asset requests are part of
that rendering operation and are not individually paced. The final browser URL must match
a reviewed origin/path before extraction. That final check rejects off-scope results; it
does not prevent a browser-internal redirect from making its request.

For HTTP 429 or 503, `Retry-After` seconds and HTTP-date values extend the shared cooldown.
Stagehand forwards the source response's header, and returns error pages immediately
instead of waiting for a listing selector that will never appear. A busy Stagehand service
also contributes its backoff. Missing, past or malformed advice never shortens the minimum
interval. A cooldown longer than `SITE_REQUEST_TIMEOUT_SECONDS` causes the task to defer
by failing without fetching, preserving the reservation for later Airflow retries/runs;
it does not occupy a worker by sleeping for hours. Snapshot/cache replay requires no fetch.

## Inspection and verification

```sql
SELECT source, fetch_method, enabled, access_policy->>'status' AS review_status,
       access_policy->>'reviewed_at' AS reviewed_at,
       min_request_interval_seconds, next_fetch_at
FROM ingest.source_adapter
ORDER BY source;
```

`ingest.run` records failed/partial runs; `ingest.page_fetch` records source HTTP statuses,
including 429/503, and transport failures. Successful extraction plans and model/prompt
provenance remain in the existing Postgres tables.

Offline tests exercise concurrent database workers, failed-request pacing, server advice
in both formats, long cooldowns, malformed reviews, revocation during a wait, HTTP redirect
gating and browser result boundaries. Synthetic Chromium tests verify backoff forwarding
without model calls or live scraper traffic. Migration tests cover forbidden enablement
and both migration directions.
