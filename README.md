# Event Discovery Platform

A map-first progressive web app for discovering in-person events collected from multiple
sources. The product will combine a React map interface, a FastAPI service, Postgres with
PostGIS, Airflow ingestion, Neo4j relationships, Qdrant deduplication, Redis, and a
Stagehand browser-automation worker.

`PROJECT_PLAN.md` is the product and technical specification. `CONTRIBUTING.md` records
the required delivery workflow.

## Status

Repository scaffold only. No application phase has been implemented yet.

## Repository layout

- `api/` — FastAPI application
- `web/` — React progressive web app
- `airflow/` — ingestion DAGs and tasks
- `stagehand/` — browser-automation worker
- `tests/` — shared fixtures and cross-service tests
- `.github/workflows/ci.yml` — continuous integration
- `docker-compose.yml` — local service orchestration

## Development

Read `PROJECT_PLAN.md` and `CONTRIBUTING.md` before starting a sub-phase. Local setup and
run commands will be added with Phase 1a as the first runnable services are introduced.
