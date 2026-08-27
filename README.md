# Event Discovery Platform

A map-first progressive web app for discovering in-person events collected from multiple
sources. The product will combine a React map interface, a FastAPI service, Postgres with
PostGIS, Airflow ingestion, Neo4j relationships, Qdrant deduplication, Redis, and a
Stagehand browser-automation worker.

`PROJECT_PLAN.md` is the product and technical specification. `CONTRIBUTING.md` records
the required delivery workflow.

## Status

Phase 1 is in progress. The PostGIS, FastAPI, and React service skeletons run through
Docker Compose with health checks.

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

The initial endpoints are:

- Web: `http://127.0.0.1:3000`
- API health: `http://127.0.0.1:8000/health`
- Seeded events GeoJSON: `http://127.0.0.1:8000/api/events`

Run the Phase 1 container integration check without occupying the default host ports:

```bash
bash tests/phase1-compose.sh
```
