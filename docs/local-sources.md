# Local source inventory (Phase 8c)

Migration `20260927_0014` adds local source adapters, reviewed listing targets and the
`baltimore-md` market. Philadelphia continues to use `philadelphia-pa`; Phoenixville is
part of that market, while its venue retains its actual city. No ingestion module branches
on a city, market slug or source name.

| Source | Market | Fetch | Initial scope | Recorded events |
| --- | --- | --- | --- | --- |
| [Reads & Company](https://www.readsandcompany.com/events) | Philadelphia area | Stagehand | In-store book groups; excludes Colonial Theatre, Bistro, online and virtual listings | 8 |
| [PhilaMOCA](https://www.philamoca.org/) | Philadelphia | HTTP | Linked public venue events; excludes private and offsite listings | 55 |
| [Charm City Books](https://www.charmcitybooks.com/events) | Baltimore | Stagehand | Listings explicitly located at “The Bookshop!”; excludes online/virtual listings | 9 |

Counts describe the September 27, 2026 captures, not a guaranteed future inventory.
The two bookstore HTTP responses contain an empty React root and no listings. The
Stagehand container rendered both calendars successfully. No documented public event API
was found in the reviewed public pages; their private browser backend is not an API adapter.
PhilaMOCA already serves its event cards in HTML and uses the ordinary HTTP DAG.

## Access review before enablement

The migration records the review outcome, date, evidence URLs, allowed listing URLs and
notes in `ingest.source_adapter.access_policy`, and a 10-second minimum page-request
interval in `min_request_interval_seconds`. This is the portion of 8d necessary to enable
8c sources, as required by the Phase 8 instructions.

Reviewed on September 27, 2026:

- Reads & Company: rendered calendar, navigation, footer and
  [robots.txt](https://www.readsandcompany.com/robots.txt). No dedicated site ToS link was
  found in the reviewed surfaces. Robots directives restrict dotbot under `/i/`; they do
  not disallow this public calendar. Scope is event facts and source links for in-store
  book groups. Book synopses, images and offsite events are not published by this adapter.
- Charm City Books: rendered calendar, navigation, footer and
  [robots.txt](https://www.charmcitybooks.com/robots.txt). No dedicated site ToS link was
  found in the reviewed surfaces. The same dotbot-only directive applies. The bounded
  scope is event facts and source links for the explicitly named in-store location.
- PhilaMOCA: public calendar, footer and
  [robots.txt](https://www.philamoca.org/robots.txt). No dedicated site ToS link was found.
  The calendar path is allowed; the published `Crawl-delay: 10` is respected. Administrative
  and internal paths are outside the reviewed scope. Only event facts and outbound event
  links are normalized; posters and descriptive prose are not published.

The linked [Bookmanager privacy policy](https://bookmanager.com/privacy-policy) was also
reviewed for the bookstore platform. It describes account, transaction and browsing-data
handling; it does not supply an event-content reuse license or a public event API.

“Reviewed” records an operational review; absence of a prohibition is not a license or
written permission from the owner. Re-review before widening scope or when policy changes.
Disable the adapter to stop future fetches. When recording `access_policy.status = blocked`,
set `enabled = false` in the same update; the database rejects enabled, unreviewed scrapers.
Do not interpret successful HTTP access alone as an access review.

The production fetch task fails closed for disabled/unreviewed sources or URLs outside
the reviewed origin and path. Query parameters may vary for reviewed pagination. Every
HTTP navigation, including redirects, acquires the source's Postgres advisory lock. A
Stagehand fetch holds that same lock across rendering, and replayed pagination clicks are
spaced by the configured interval. The interval starts after completion, including failed
requests. A reservation also survives a killed worker until request timeout plus interval.
Workers and markets share this state; cached page replay makes no outbound request.
Browser asset requests needed to render a page are not individually delayed. These initial
sources have one listing page and no pagination actions.

See [source-access.md](source-access.md) for the enablement gate, review procedure,
server-requested backoff and operational inspection added in Phase 8d.

## Extraction and identity

All selectors are model-derived plans cached in `ingest.extraction_plan`; migrations do
not seed selectors or plans. Fixtures include recorded plans for offline testing only.
The selected derivation model is pinned in each source row. Model selection is operational
inventory, and credentials/endpoints remain environment variables. The initial smaller
models produced inconsistent plans on real markup; the selected model and replay checks
are documented in the fixture provenance.

The shared plan interpreter supports multiple DOM parts, optional document scope, direct
text nodes (including the scoped root), optional text parts, literal range splitting and
ordinal-day removal before strict date parsing.
The `site-plan-v2` cache fingerprint invalidates old interpreter plans. Existing validation,
cache failure handling, normalization, geocoding, deduplication and graph projection apply.

Verified fixed venue fields live in each adapter's extraction configuration. They are used
only with that adapter's documented in-venue filter. The model cannot override those fields.
Do not broaden filters without reviewing venue handling. Bookstore listings link to their
calendar because native event links and IDs are not present in the DOM. All three sources
use a deterministic hash of title, start and venue address when a native ID is unavailable;
PhilaMOCA ticket links can repeat for a series. A title/time edit can therefore create a new
source identity; the existing deduplication and stale-listing paths remain responsible for
reconciliation. These adapters intentionally omit descriptions and unknown end times.

## Running locally

Set `EXTRACTION_API_KEY` and the existing Stagehand connection/token environment variables.
After applying the migration and rebuilding the Airflow image, the existing DAGs discover
the new rows automatically. The default nightly schedules are `0 4 * * *` for browser
sources and `30 3 * * *` for HTTP sources, in UTC; their environment variables can override
these. Compose sets new DAGs to unpaused. No city-specific DAG is added.

From the repository root:

```sh
rtk docker compose build api airflow-scheduler airflow-dag-processor airflow-api-server stagehand
rtk docker compose run --rm api alembic upgrade head
rtk docker compose up -d stagehand airflow-scheduler airflow-dag-processor airflow-api-server
```

The first successful extraction derives a plan. Subsequent runs replay it until the source
configuration changes or validation fails. A failed fresh plan is recorded and stops that
snapshot rather than repeatedly billing on task retries. Look at `ingest.run`,
`ingest.page_fetch`, `ingest.site_page` and `ingest.extraction_plan` for outcomes and provenance.
Mapped task groups let each source/market finish independently when another target fails.

## Verification

`airflow/tests/test_local_sites.py` uses recorded DOM and model plans, deterministic
geocoding/embedding transports, real Postgres/PostGIS and Neo4j. It checks both markets,
cache reuse across nights, retry idempotency, missing JS listings in plain HTTP, provenance,
and the existing complete downstream pipeline. Policy tests exercise the shared database
lock, failed-request pacing, reviewed URL boundaries, blocked sources and browser action
spacing. The new API migration test checks inventory, constraints and upgrade/downgrade.
Existing site/DAG tests continue to cover pagination, invalidation and validation failures.
Automated tests make no paid calls or live source requests. Manual capture/derivation is a
separate onboarding activity, never a pytest fixture that goes online.
