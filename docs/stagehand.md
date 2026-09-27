# Stagehand worker (Phase 8a)

The `stagehand/` Node service contains Stagehand and Chromium. Airflow receives only
`STAGEHAND_BASE_URL` and `STAGEHAND_API_TOKEN`; its image and Python dependencies are
unchanged. This worker is stateless: it returns rendered pages and stores no inventory,
listings, extraction plans, or credentials. Postgres remains the system of record.

Copy the Stagehand settings from `.env.example` into your local `.env`, then run
`docker compose up --build --detach --wait stagehand`. Startup launches and closes a
browser before accepting requests, so missing browser dependencies fail startup.
The HTTP port and Chromium debugging ports are not published to the host.

## HTTP contract

- `GET /health` returns `{"status":"ok"}` while the process accepts work. It does not
  launch a browser on every health check; browser availability is checked at startup.
- `POST /v1/fetch` requires `Authorization: Bearer <STAGEHAND_API_TOKEN>` and JSON:

  ```json
  {"url":"https://example.org/events","ready_selector":"main article"}
  ```

  Only HTTP(S) URLs without embedded credentials are accepted. `ready_selector` is
  optional: it waits for rendered content after the page's load event, within the overall
  deadline. It is a readiness condition, not an extraction plan. Without it the snapshot
  is taken after load, which may precede asynchronous listings. The caller must supply
  readiness appropriate to the source when needed.

  Successful responses contain `url` (final URL after redirects), `http_status` (upstream
  navigation status, or null if unavailable), `title`, `html` (rendered DOM), `bytes`
  (UTF-8 DOM size), and `fetch_method: "stagehand"`. An upstream 404/429 is preserved in
  `http_status`; it is not misrepresented as a successful source fetch. Callers must check it.

Errors are JSON with an `error` code: 400 `invalid_request`, 401 `unauthorized`,
413 `request_too_large`, 503 `browser_busy` (with `Retry-After: 1`),
504 `fetch_timeout`, or 502 `browser_failure` / `page_too_large`.
Target URLs, rendered content, and underlying browser error messages are not logged.

## Runtime boundaries

Each request launches a fresh browser profile and closes it on success or failure.
`STAGEHAND_MAX_CONCURRENCY` limits concurrent requests; excess work is rejected rather
than accumulating in memory. This resource limit is **not per-source rate limiting**.
The fetch deadline includes browser startup, navigation, readiness, and capture. Cleanup
is awaited before freeing capacity or responding; a timed-out launch may take up to the
SDK's own startup timeout to finish and close. Allow extra HTTP client timeout for this
cleanup. SIGTERM drains requests; Compose allows 90 seconds before forced termination.

All process settings are required environment variables, documented in `.env.example`.
The container runs as `node`, with an init process to reap Chromium children. Chromium's
nested sandbox is disabled for default Docker compatibility and can be enabled with
`STAGEHAND_BROWSER_SANDBOX=true` on a supporting host. Treat this as a trusted internal
service: authorized callers can navigate to network destinations reachable by the worker.
It is not a public URL-fetching API or an SSRF isolation boundary.

## Verification and the next gate

From `stagehand/`, run `npm ci`, `npm run lint`, `npm run typecheck`, `npm run build`,
and `npm test`. From the repository root, `bash tests/phase8a-compose.sh` builds and runs
real Chromium tests with `--network none`, then verifies the production Compose image.
The synthetic site creates listings exclusively with JavaScript. Tests cover rendered
DOM, redirects, upstream errors, profile isolation, timeouts, size limits, cleanup,
authentication, request validation, and capacity recovery. They need no model credentials.

Phase 8b adds extraction configuration, the shared HTTP/browser extraction step, model
and prompt provenance, validation, persistent derive/replay caching, pagination, and DAG
integration through the existing pipeline. No inference or event parsing is implemented
in 8a. Existing ordinary HTTP adapters continue unchanged. No source is enabled here;
ToS review and per-source rate limiting must precede onboarding in the later sub-phases.
