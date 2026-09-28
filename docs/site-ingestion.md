# Site ingestion (Phase 8b)

`ingest_site_stagehand` runs at 04:00 and `ingest_site_generic` at 03:30 by default.
Their schedules, service endpoints, credentials, timeouts, and payload size ceiling are
environment settings in `.env.example`. Both DAGs use the same extraction and ingestion
code. Only the fetch transport differs. An empty enabled inventory is a successful no-op;
no local sites are enabled by this migration.

## Inventory and activation

`ingest.source_adapter` owns each source's `fetch_method`, merge `priority` (lower wins),
`extraction`, `pagination`, `model`, `prompt_version`, and `enabled`.
Eventbrite and Meetup are seeded as existing API adapters with null extraction settings.
Deduplication and manual merges now read priority from this table; the former
`DEDUP_SOURCE_PRIORITY` environment setting is no longer used. Disabled sources retain
their merge priority for already-ingested listings.

A site adapter has `extraction` shaped as:

```json
{
  "instruction": "Extract each in-person event, its stable ID, registration URL, title, start time, and full street address.",
  "schema": {
    "type": "object",
    "required": ["source_event_id", "url", "title", "starts_at", "venue_address"],
    "properties": {
      "source_event_id": {"type": "string", "minLength": 1},
      "url": {"type": "string"},
      "title": {"type": "string", "minLength": 1},
      "starts_at": {"type": "string"},
      "venue_address": {"type": "string", "minLength": 1}
    }
  },
  "ready_selector": "main"
}
```

The schema validates **one event**, not a page. Optional shared fields are `description`,
`ends_at`, `venue_name`, `venue_city`, `venue_region`, and `venue_country`. The shared
validator always requires the five core fields, checks HTTP URLs and ISO start/end times,
and rejects an end before its start. Source schemas may strengthen those checks. Schema
references are disallowed; validation never fetches remote resources. Naive timestamps
use the canonical market's timezone. Actual address resolution remains in the existing
geocoding pipeline; failures invalidate the associated extraction plan.

Site crawl targets use adapter-owned `source_location` JSON:
`{"kind":"listing_url","url":"https://reviewed-source.example/events"}`. They point to
ordinary `ingest.market` rows and carry the source/category/page-cap inventory. API adapters
keep their existing location shapes. Source URLs, model choice, schema, instructions,
pagination, and market identity are never environment variables or city-specific code.
This adapter crawls the supplied listing URL; it does not invent date-query parameters
from `window_days`. Its run date-window columns remain unset rather than claiming a
source-side date filter was applied. The original targets remain in the run snapshot.

Create site rows disabled. Phase 8b does not enable any local source. Before onboarding
or enabling one in 8c, complete the per-source rate limits and actual ToS review required
by 8d. Those policies and reviews are deliberately outside this sub-phase's review gate.

## Pagination

`pagination.kind` supports:

- `none`: one page.
- `query`: `{"kind":"query","parameter":"page"}`; pages start at one.
- `next_link`: the model-derived plan identifies the next link. URLs must remain on the
  configured origin. A disabled or absent next control ends the crawl.
- `action`: browser-only; for example
  `{"kind":"action","wait_for":"main[data-page='{page}']"}`. The model derives the next
  button selector. The readiness template must prove that the requested page has appeared.
  The stateless worker replays prior clicks from the original URL, waiting
  for each page's readiness condition. At most 20 clicks are replayed per request.

`ready_selector` waits for the initial page state, not extraction fields. Use a container
that exists on both populated and empty pages. Explicit empty-state markers and repeated
ID sets terminate pagination. URL cycles, upstream errors (including 429), and exhausted
page caps close the run as `partial`, preserving successful pages. Transport and extraction
failures mark the run `failed`. Every attempted page has a `page_fetch` record; transport
failures use synthetic status 599.

## Derive once, validate, replay

The model is used to derive a declarative DOM plan from the fetched page and source
configuration, using the Responses API's
[structured output contract](https://developers.openai.com/api/docs/guides/structured-outputs).
`EXTRACTION_API_URL` and `EXTRACTION_API_KEY` are required only on a cache miss or invalid
plan. Requests disable provider-side response storage. No model is chosen by this migration;
onboarding must select a model supporting that API and structured output schema.

Plans contain event-card/empty-state/next-control selectors and field rules: relative
selector, attribute or text, and optional `strptime` format. They contain no executable
code and no inferred literal event values. The model derives selectors; operators provide
the semantic instruction and schema. This is the deterministic replay implementation of
the model-driven extraction design. Pages whose dates/fields cannot be represented in
this bounded language fail validation rather than quietly fabricating event data.

`ingest.extraction_plan` stores the validated plan, actual response model, prompt version,
and derivation time. Its key includes source plus a hash of fetch method, model, prompt
version, extraction config, pagination config, and replay-engine version. Bump the engine
version if changing the shared derivation prompt or replay semantics. A source-specific
Postgres advisory lock prevents concurrent workers from paying to derive the same key.

Every replay checks source schema and shared field validity. Missing cards count as a
failure unless a specific empty-state marker matches. A cached-plan failure deletes the
plan and permits one fresh derivation. An invalid fresh plan is never cached. That page's
failure is persisted, so an Airflow retry does not pay again for the same invalid snapshot;
a new scheduled run can retry with a new page. Transient model HTTP failures remain
retryable. Geocode failures also invalidate the plan and affected extracted page result.

`ingest.site_page` retains the fetched HTML, validated event JSON, configuration hash,
model/prompt provenance, cache-hit flag, and validation-failure count. The linked
`ingest.page_fetch` supplies source-run/target/page identity and errors. A retry reuses its
successful fetch snapshots. Source/target configuration is snapshotted in
`ingest.run.source_config`. `source_listing` receives the deduplicated raw extraction
envelope and its `extraction_model` / `extraction_prompt_version` columns before the
shared parser/filter runs. The hourly geocode, canonicalization, dedup, and graph jobs
consume these listings through the existing pipeline. Postgres remains authoritative.

## Operational limits

Cross-origin pagination is rejected. Browser steps have a bounded action count and an
overall fetch deadline; they do not implement per-source rate limiting. Chromium's
isolation/security constraints are documented in [stagehand.md](stagehand.md).

HTML snapshots are retained in Postgres for diagnosis and replay. This phase adds no
automatic retention job. Monitor their size before large-scale onboarding. Browser action
pagination replays earlier clicks, so its navigation cost grows with page depth; keep page
caps modest and configure worker/client timeouts for the required waits. The model receives
DOM markup with script/style elements removed. `SITE_MAX_HTML_BYTES` limits captured
pages, while `EXTRACTION_MAX_INPUT_BYTES` separately caps markup sent to the model.
Oversized input fails rather than deriving from a silently truncated page.

## Verification

`airflow/tests/test_sites.py` exercises replay, invalidation, validation failures, pagination,
provenance, idempotency, and the full Postgres → geocode → dedup →
Neo4j path. `test_site_dag.py` executes both real DAGs, using deterministic browser/model
HTTP substitutes. `api/tests/test_site_migration.py` verifies constraints and migration
round trips. The worker's real Chromium tests run with networking disabled via
`bash tests/phase8a-compose.sh`. All page/model fixtures are synthetic and labelled under
`tests/fixtures/site/`; no paid model calls or live scraper requests are used by the suite.

## Local sources

Phase 8c seeds reviewed Philadelphia-area and Baltimore calendars. See
[local-sources.md](local-sources.md) for scope, access policies, pacing, recorded fixtures and
activation instructions. New generic plan capabilities are versioned as `site-plan-v2`;
configuration changes or that engine change invalidate the cached plan.
