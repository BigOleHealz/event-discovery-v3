# Dedup review (Phase 4f)

Open `/admin/dedup` on the web service. The queue presents the original pair's titles,
descriptions, venues, times in their event timezones, and source links. Merge keeps the
canonical event preferred by the existing `DEDUP_SOURCE_PRIORITY` configuration and moves
all listings to it. Distinct and Skip leave both events intact. M / D / S perform the same
actions as the buttons; shortcuts are disabled while typing or submitting a decision.
Skip records a skipped decision and removes that pair from the pending queue.

Google sign-in and the session interface belong to Phase 6. For now the API requires a
bearer token on every review read and write. Access defaults to disabled. Configure these
**server-only** variables in your untracked `.env`:

- `ADMIN_REVIEW_TOKEN`: a random secret, e.g. generated with `openssl rand -hex 32`.
- `ADMIN_REVIEW_USER_ID`: a stable UUID for the person holding that token.

Rebuild and recreate the services with the new environment:

```sh
rtk docker compose up -d --build api web
rtk docker compose up -d --build airflow-api-server airflow-scheduler airflow-dag-processor
```

After migrations, API startup automatically creates a provisional (`is_shadow`) app_user
when both admin settings are present. Restarts never overwrite an existing user. This runs
independently of `SEED_DATABASE`; building an image alone does not access the database.
Phase 6 can claim that identity. Enter the
token in the review page to unlock it. It stays in memory only, is never built into the
browser bundle, and is cleared on Lock or reload. Use HTTPS outside local development.

Migration 0010 backfills unresolved 4d review outcomes and installs a trigger that queues
new ones in the same transaction as dedup. Sorted listing IDs and a unique constraint make
retries and reversed pairs idempotent. Immutable evidence snapshots, scores, metrics, status,
reviewer ID, and decision timestamp remain in `dedup_review` as labelled data after a merge.

Decisions serialize with 4d through its existing advisory lock. Repeating the same decision
returns the recorded result; a conflicting or stale decision returns 409. A Distinct label
blocks later automatic or manual merging of the two canonical groups. Dependent user data
that cannot be moved safely causes the entire merge to roll back.
Pending and skipped pairs also block automatic merges between their canonical groups, so
a rerun or a new embedding cannot silently bypass the reviewer.

The existing source-priority environment setting is reused here. Moving priority to
`ingest.source_adapter` stays with that registry's introduction; it does not exist yet.

Tests run against disposable PostGIS/pgvector databases. `tests/phase1-e2e.sh` seeds both a
resolved two-source event and review pairs, sets fixture-only admin credentials, and runs
the browser tests against Compose. Use a separate `COMPOSE_PROJECT_NAME` to keep that test
stack separate from your development database.
