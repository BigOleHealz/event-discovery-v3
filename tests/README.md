# Tests

Shared fixtures and cross-service tests belong here. Service-specific tests may live beside
their implementation when that makes ownership clearer.

The full testing rules are documented in `PROJECT_PLAN.md` section 8 and
`CONTRIBUTING.md`.

`bash tests/phase1-e2e.sh` runs the browser suite, including Phase 3g, against a
fresh, uniquely named Compose project with ephemeral ports. It builds the production
web app/service worker, seeds only that project's PostGIS database, replays Google Maps,
and removes the test project's volumes on exit. Event requests in the offline tests
use the real API; no scraper, model, or paid map API is called.
