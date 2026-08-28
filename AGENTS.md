# AGENTS.md

Event discovery platform.

- `PROJECT_PLAN.md` — the specification. What we're building and why.
- `CONTRIBUTING.md` — the working rules. Branching, pacing, commits, tests, code quality.
  **Read it before starting any sub-phase.** Where this file and CONTRIBUTING.md overlap,
  CONTRIBUTING.md wins.

This file covers orientation and context hygiene only.

## Orientation

Read only what the current task needs. `PROJECT_PLAN.md` is long; read the section named in
the prompt (`§5`, `§3.2`) rather than the whole file. Section numbers are stable.

The roadmap is §11. For where the work actually stands, run `git log --oneline -5` and
`rtk git status` — not a line in this file, which would be stale within a day.

**Never commit or push without explicit authorization from the maintainer**, including when
a task prompt says to commit. See CONTRIBUTING.md — that language is the intended eventual
handoff, not permission. Show the diff and test results, then stop.

## Two design decisions that are easy to "helpfully" undo

**Config has two homes, and the split is deliberate (§9, rule 1).** Process config —
service connections, credentials, API base URLs, schedules, secrets — is environment
variables, never baked into images and never in the database. Operational inventory —
which markets and crawl targets are enabled — is application data in `ingest.market` and
`ingest.crawl_target`, seeded and changed by migration. If you find yourself adding
`EVENTBRITE_CATEGORIES` or a comma-separated location list to `.env`, stop: that belongs in
`ingest.crawl_target`. Enabling a category in one market must not silently enable it
everywhere.

**Source adapters own their own location shape.** `ingest.crawl_target.source_location` is
adapter-validated JSON with no universal schema — Eventbrite uses
`{"kind":"eventbrite_slug","slug":"pa--philadelphia"}`, another source may use coordinates
and a radius or a bounding box. Do not normalize these into a shared format. They group
under a canonical `ingest.market`, and that is the only thing they have in common.

## Context hygiene

Context is a budget. Spending it on boilerplate leaves less for the actual problem.

**Never `cat` files in `tests/fixtures/`.** They are large raw API payloads. Inspect
structure with `jq 'keys'` or `jq -r 'paths(scalars) | join(".")' | sort -u | head -50`;
sample a value with `jq '.events[0].name'`. For a raw look, `head -c 500`.

**Show diffs, not files.** For a file already discussed this session, show `git diff`. Print
a whole file only when it's new or when asked.

**Don't re-read files you've already read** this session unless they changed.

**Don't paste command output back to me.** I can see it. Summarise the result.

**Search, don't scan.** `rg 'pattern'` to find code. Don't read whole modules looking for
something.

**On a truncated failure, open the tee log.** RTK saves full unfiltered output on failure
and prints the path. Read it rather than re-running the command or guessing. Never skip a
diagnosis because output was compact. See `RTK.md`.

## Maintaining these documents

Do not update `PROJECT_PLAN.md`, `CONTRIBUTING.md` or this file as part of implementing a
sub-phase. Progress is recorded by commits, not by editing the spec.

Propose an edit only when implementation shows a document is **wrong** — a decision that
doesn't survive contact with the code, a constraint that turned out to be impossible, a
schema detail that changed. Report it, wait for authorization, then edit in a commit of its
own, separate from code.

When editing: change the part that's wrong and nothing else. Do not condense, reorganize,
or improve surrounding prose. The rationale paragraphs are load-bearing — they exist so
that a future session doesn't re-argue a settled decision, and they are the first thing
lost to well-intentioned tidying. A shorter plan is not a better one.

## Commands

```bash
pytest                      # quiet by default, see pyproject.toml
pytest -x                   # stop at first failure — use while iterating
pytest tests/path::test_x   # single test
docker compose up -d
alembic upgrade head
ruff check . && mypy .
```

@RTK.md
