# Event Discovery Platform

A map-first progressive web app for discovering in-person events collected from multiple
sources. The product will combine a React map interface, a FastAPI service, Postgres with
PostGIS, Airflow ingestion, Neo4j relationships, Qdrant deduplication, and a
Stagehand browser-automation worker.

`PROJECT_PLAN.md` is the product and technical specification. `CONTRIBUTING.md` records
the required delivery workflow.

## Status

Phase 1 provides a containerized PostGIS database, FastAPI GeoJSON endpoint, and an
installable React map PWA backed by 20 seeded Philadelphia events.

Phase 2 ingestion starts with nightly Eventbrite public listing-page discovery. It records
page metadata, deduplicates event ids across configured categories, fetches and TTL-caches
official detail payloads, stages each untouched detail in `source_listing.raw_payload`, and
only then parses the listing so parser failures can be retried without another API call.
Parsing rejects source-declared online events first, then virtual venue names or addresses,
then online wording only when no physical venue exists, and finally listings with no usable
address or coordinates. Hybrid events with a physical venue remain eligible.

## Repository layout

- `api/` — FastAPI application
- `web/` — React progressive web app
- `airflow/` — ingestion DAGs and tasks
- `stagehand/` — browser-automation worker
- `tests/` — shared fixtures and cross-service tests
- `.github/workflows/ci.yml` — continuous integration
- `docker-compose.yml` — local service orchestration

## Development

Read `PROJECT_PLAN.md` and `CONTRIBUTING.md` before starting a sub-phase.

Copy the local environment template, add a browser-restricted Google Maps API key when map
work lands, then start the stack:

```bash
cp .env.example .env
docker compose up --build --wait
```

For the map, enable the Google Maps JavaScript API, set `GOOGLE_MAPS_API_KEY`, and provide a
JavaScript map ID in `GOOGLE_MAPS_MAP_ID`. The example uses Google's `DEMO_MAP_ID` for local
testing; production should use a project-owned map ID and an HTTP-referrer-restricted key.

For Eventbrite ingestion, set `EVENTBRITE_API_TOKEN` to the app's private token. Public
listing pages supply discovery ids; the official per-event endpoint supplies parser-ready
detail payloads. `ingest.market` defines source-independent markets, while enabled rows in
`ingest.crawl_target` define each source's native location JSON, category, window size, and
page cap. The DAG groups those rows into one independently tracked run per canonical market
and unions ids before detail calls.
API credentials, schedules, request timeouts, and the detail-cache TTL remain environment
configuration.

The hourly geocoder uses Google Geocoding API v4. Set `GOOGLE_GEOCODING_API_KEY` to a
server-side key restricted to the Geocoding API; do not reuse the browser-referrer key.
Normalized input addresses are cached to canonical `venue` rows, so recurring venues do
not spend another API call.
After geocoding, each Eventbrite listing is upserted into its own `canonical_event` and
linked through `source_listing`; later scrapes update that same event. Cross-listing dedup
is intentionally deferred to Phase 4.
The event GeoJSON includes one validated registration link per source. Selecting a map pin
opens a responsive detail slide-over with the event time, venue, description, and source
registration buttons.

The initial endpoints are:

- Web: `http://127.0.0.1:3000`
- API health: `http://127.0.0.1:8000/health`
- Events GeoJSON: `http://127.0.0.1:8000/api/events`

Run the Phase 1 container integration check without occupying the default host ports:

```bash
bash tests/phase1-compose.sh
```

Run the complete browser acceptance flow (real containers and database, deterministic
Google Maps transport fixture, installability checks, and an offline reload):

```bash
npx --prefix web playwright install chromium
bash tests/phase1-e2e.sh
```

Chrome removed Lighthouse's standalone PWA score in 2025. The browser flow therefore
uses Chromium's current installability diagnostics directly and verifies the manifest,
physical icon dimensions, service-worker control, precached shell, and offline reload.
