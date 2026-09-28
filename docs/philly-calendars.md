# Philadelphia calendar additions

Migration `20260928_0016` adds `grid-magazine`, `bartrams-garden`, and `fleisher` to
`ingest.source_adapter` and the existing Philadelphia market's `ingest.crawl_target` rows.
Grid uses `ingest_site_generic` and ordinary HTTP. Bartram's and Fleisher use
`ingest_site_stagehand`: local HTTP returned Cloudflare challenge pages, while ordinary
Stagehand navigation returned HTTP 200 and public event metadata without challenge interaction. No city or source names occur in the
extraction engine. The existing normalization, geocoding, canonicalization, deduplication,
and graph projection jobs consume their `site-v1` payloads.

## Access review (2026-09-28)

- [Grid Magazine](https://gridphilly.com/events/): [robots](https://gridphilly.com/robots.txt)
  has an empty wildcard Disallow. No dedicated terms/privacy link was found on the reviewed
  calendar/footer. Scope is public event facts and source links, excluding prose and images.
- [Bartram's Garden](https://www.bartramsgarden.org/calendar/):
  [robots](https://www.bartramsgarden.org/robots.txt) has an empty wildcard Disallow and
  publishes Crawl-delay: 10. No dedicated terms link was found on the reviewed calendar/footer.
  Use each event's address; the calendar includes offsite events at Clark Park.
- [Fleisher](https://fleisher.org/calendar/): [robots](https://fleisher.org/robots.txt) has an
  empty wildcard Disallow. Its linked [privacy policy](https://fleisher.org/about-us/privacy-policy/)
  contains no scraping prohibition found in this review. The calendar states that events
  occur at 719 Catharine Street unless otherwise noted. Those venue defaults apply only when
  event metadata has no location; explicit offsite or incomplete named locations are never
  replaced by defaults.

These are operational reviews, not licenses or written permission. Evidence, scope, review
status and timestamps are recorded in Postgres. All sources have a 10-second minimum interval,
with only the root calendar, its list view and page 2 allowed. The two-page cap is intentional:
when another page exists the run is marked partial, rather than claiming exhaustive coverage.

## Extraction and limits

`extraction.format = jsonld` selects a generic schema.org Event plan. A model derives JSON
Pointer field mappings and a next-page selector from a small metadata sample and navigation
markup. That data-only plan is validated and cached using the existing source/config cache.
Every event in each full page is replayed and validated; model and prompt provenance travel
with listings. No remote JSON-LD contexts are fetched and no generated code is executed.
The raw HTML remains in `ingest.site_page.html` for audit and retry.

Only event facts and source links are retained as listing fields; description is always null.
Online-only, cancelled/postponed, untimed, midnight-starting and addressless entries are skipped.
Title exclusions also remove cancellation/closure and online/virtual notices. Midnight events
are conservatively excluded because these calendars encode untimed notices with midnight
start timestamps. Missing addresses are not guessed. Extracted street, city, region and country are combined
into the address string consumed by the existing geocoder. Grid includes regional venues; their
actual locations are resolved by the existing geocoder. Its broad default category and
Bartram's are `community`; Fleisher's is `arts`.

Per-page exclusions are stored as URL/reason pairs in `ingest.site_page.extraction_skips`.
Malformed timestamps, invalid plans, invalid required fields, missing event metadata or
malformed JSON fail extraction and trigger the existing cache invalidation/re-derivation path.
A filtered-out page does not stop pagination. A wholly empty calendar without Event metadata
fails visibly, rather than silently treating a site redesign as a successful empty run.

## Local rollout

Rebuild API/Airflow images and restart their services. The API entrypoint applies Alembic
migrations. A Docker rebuild alone does not migrate a running database. New enabled inventory
is picked up by the next matching HTTP or Stagehand DAG execution; its schedule stays in environment
configuration. Downstream jobs retain their existing schedules.

Tests use recorded metadata/navigation excerpts and deterministic model, geocoder and
embedding substitutes. Real Postgres and Neo4j verify retry idempotency, cached replay,
pagination, rate limiting, exclusions, and downstream projection. No external scraper or
paid model call is part of the test suite.
