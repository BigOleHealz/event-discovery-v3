# Contributing

`PROJECT_PLAN.md` is the project specification. Read it and this file before starting any
sub-phase. Follow the plan where it is explicit. Where it is silent, use engineering
judgement and report the decision. If the plan appears wrong, raise the issue before
writing code rather than silently diverging.

## Branching

Never commit directly to `main`. Cut each phase branch fresh from the latest merged
`main`; do not carry unmerged work from one phase branch into another.

| Phase | Branch |
|---|---|
| 1 | `phase-1-skeleton` |
| 2 | `phase-2-one-source` |
| 3 | `phase-3-search` |
| 4 | `phase-4-dedup` |
| 5 | `phase-5-graph` |
| 6 | `phase-6-social` |
| 7 | `phase-7-retention` |
| 8 | `phase-8-hard-sources` |
| 9 | `phase-9-deploy` |

The maintainer merges each completed phase to `main` before the next phase branch is cut.

## Pacing and review

Work on exactly one roadmap sub-phase at a time. Show the complete diff for review before
starting the next sub-phase, even when later work appears obvious. Do not run ahead into a
later phase.

## Commits

Make one commit per roadmap sub-phase (`1a`, `1b`, `1c`, and so on), not one commit per
phase and not one commit per file. Tests and implementation for that sub-phase belong in
the same commit.

Use Conventional Commits-style subjects, for example:

```text
feat(api): add bounding-box event query
```

The commit body must explain what changed, why it changed, and identify the roadmap
sub-phase.

## Tests

A sub-phase is incomplete until its relevant tests pass. Tests must exercise real
behaviour and actual code paths; do not add smoke tests that only assert a truthy result.
Follow `PROJECT_PLAN.md` section 8, including these requirements:

- Use real Postgres/PostGIS through `testcontainers` for code that touches PostGIS. Do not
  substitute SQLite or a mocked database session.
- Commit captured `raw_payload` fixtures under `tests/fixtures/<source>/` for parser tests.
  The test suite must not make live scraper requests.
- Inject clocks into time-sensitive code instead of calling `now()` internally; freeze the
  injected clock in tests.
- Record external API traffic once and replay it in CI with `vcr.py` or hand-written
  fixtures. Tests must not spend real API calls.
- Test database constraints through observable behaviour, including deliberately
  attempting forbidden duplicate writes.
- Test DAG task idempotency by running tasks twice and verifying that the second run does
  not create duplicate data.
- Test migrations in both directions on a clean database.
- Test the PWA manifest, service worker registration, offline shell, and stale-data banner
  with Playwright when those features are introduced.

Before completing a sub-phase, run its focused tests plus the relevant linters and type
checks. Before completing a phase, run the full suite.

## Code quality and configuration

Python must have type hints throughout and pass `ruff` and `mypy`; never use a bare
`except`. TypeScript must pass strict `tsc` and must not use `any`.

Follow the twelve-factor rules in `PROJECT_PLAN.md` section 9. Read configuration from
environment variables only. Do not hardcode hostnames, ports, credentials, or secrets.
Keep `.env` untracked and keep `.env.example` current with non-secret variable names.

## End-of-phase checklist

Before asking for a phase to be merged:

1. Run the full test, lint, and type-check suite.
2. Run `docker compose up` and verify the phase's exact **Done when** condition from the
   roadmap.
3. Push the phase branch.
4. Report what was built.
5. Report decisions made where the plan was silent.
6. Flag anything shaky or incomplete.
7. Report anything in the plan that implementation showed to be wrong.

Do not begin the next phase until the current phase is merged to `main` and a fresh branch
has been cut from that updated `main`.
