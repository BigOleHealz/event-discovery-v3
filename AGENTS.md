# AGENTS.md

Event discovery platform. Read `PROJECT_PLAN.md` for the specification — it is the source of
truth. This file is how we work, not what we're building.

## Orientation

Read only what the current task needs. `PROJECT_PLAN.md` is long; read the section named in
the prompt (`§5`, `§3.2`) rather than the whole file. Section numbers are stable.

Current position: **Phase 3, through 3b.** The roadmap is §11.

Source adapters own their own location shape. `ingest.crawl_target.source_location` is
adapter-validated JSON with no universal schema — Eventbrite uses
`{"kind":"eventbrite_slug","slug":"pa--philadelphia"}`, another source may use coordinates
and a radius or a bounding box. Do not normalize these into a shared format. They group
under a canonical `ingest.market`, and that is the only thing they have in common.

## Working rules

**Branching.** One branch per phase, cut fresh from `main`: `phase-3-search`,
`phase-4-dedup`, and so on. Never commit to `main`. Never carry work between phase branches.

**Commits.** One per sub-phase (3a, 3b, 3c…), not one per phase and not one per file.
Conventional-commit subjects: `feat(api): bounding-box event query`. The body names the
sub-phase and says why, not just what.

**Pacing.** One sub-phase at a time. Show the diff, wait, then move on. Do not run ahead
into later sub-phases even when the next step looks obvious.

**Push at end of phase**, after the suite passes and the phase's "done when" line in §11
actually holds — verified, not asserted.

**Conflicts.** If a prompt contradicts `PROJECT_PLAN.md`, stop and say so before writing
code. Do not silently work around it. If the plan itself looks wrong once you're
implementing, say that too.

## Tests

Tests ship in the same commit as the code they cover, never as a follow-up. A sub-phase is
not done until they pass. Full strategy is §8 of the plan.

- Real Postgres via testcontainers for anything touching PostGIS. Never SQLite, never a
  mocked session — the spatial behaviour is the thing under test.
- Parsers run against committed fixtures in `tests/fixtures/`. No live network in the suite.
- Inject clocks. Never `datetime.now()` in anything touching `starts_at`, archival, quiet
  hours, or notification timing.
- Test behaviour, not execution. "The function returned something" is not a test.

## Token hygiene

Context is a budget. Spending it on boilerplate means less room for the actual problem.

**Never `cat` files in `tests/fixtures/`.** They are large raw API payloads. To inspect
structure use `jq 'keys'` or `jq -r 'paths(scalars) | join(".")' | sort -u | head -50`.
To see a sample value use `jq '.events[0].name'`. If you need a raw look, `head -c 500`.

**Show diffs, not files.** When reporting a change to a file already discussed in this
session, show `git diff` for it. Only print a whole file when it's new or when I ask.

**Don't re-read files you've already read** this session unless they changed.

**Don't paste command output back to me.** I can see it. Summarise the result.

**Use `rtk` wrappers** for noisy commands where available: `rtk pytest`, `rtk git status`,
`rtk git diff`, `rtk git log`, `rtk ruff check`, `rtk tsc`, `rtk docker ps`, `rtk ls`,
`rtk grep`. On failure RTK saves the full untruncated output to a log file and prints the
path — read that if the compact output isn't enough to diagnose. Never skip a diagnosis
because output was truncated; open the tee log instead.

**Search, don't scan.** `rg 'pattern'` to find code. Don't read whole modules looking for
something.

## Commands

```bash
pytest                      # quiet by default, see pyproject.toml
pytest -x                   # stop at first failure — use while iterating
pytest tests/path::test_x   # single test
docker compose up -d
alembic upgrade head
ruff check . && mypy .
```

## Style

Python: `ruff` and `mypy` clean, typed throughout, no bare excepts. TypeScript: `tsc`
strict, no `any`.

**Config has two homes, and the split is deliberate (§9, rule 1).** Process config —
service connections, credentials, API base URLs, schedules, secrets — is environment
variables, never baked into images and never in the database. Operational inventory —
which markets and crawl targets are enabled — is application data in `ingest.market` and
`ingest.crawl_target`, seeded and changed by migration, never an env var list.

If you find yourself adding `EVENTBRITE_CATEGORIES` or a comma-separated location list to
`.env`, stop: that belongs in `ingest.crawl_target`. Enabling a category in one market must
not silently enable it everywhere.
