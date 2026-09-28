# Post-event feedback (6c)

`request_event_feedback` turns past `attending` records into `attended` and creates a
persistent in-app prompt for each registered attendee. **Event feedback** in the account
controls opens the queue. Attendees submit a 1–5 rating and optional text, and can revisit
or edit their feedback under Reviewed. Invite rows/edges remain intact as provenance.

## Timing and delivery decisions

The DAG defaults to 10:00 UTC daily, configured by `EVENT_FEEDBACK_DAG_SCHEDULE`.
Eligibility uses the event's timezone: its end date must be before today's local date.
Events ending today or still in progress remain ineligible. Where `ends_at` is missing,
use the start date and wait until the following local day; do not invent a duration.
The job includes older eligible records to recover missed runs. DST boundaries follow
Postgres timezone rules, rather than assuming each day is 24 hours.

A prior RSVP becomes presumed attendance when the job runs, as specified for 6c.
`no_show` records are left alone. Shadow attendance also transitions, but an in-app prompt
requires a claimed/registered account; a later run can queue it after claim. Feedback
already submitted is never overwritten by the job.

Delivery in this phase is **in-app**. `event_feedback_request` is a durable queue keyed by
attendance ID; there is no external email, SMS, or push send. `notification_log` continues
to mean an actual external delivery and is not filled with unsent notifications. External
notification transports belong to the later notification work.

## State, API, and retries

- Attendance transition, prompt creation, and success bookkeeping commit together.
  Failure rolls back attendance/prompts and records the failed attempt in `ingest.run`.
- A global advisory lock serializes job invocations. The request's primary key suppresses
  duplicate prompts; deterministic run IDs let retries update the same audit record.
- `GET /api/attendance/feedback` returns the session owner's outstanding requests.
  `completed=true` returns submitted feedback. Both accept `limit` (1–100, default 50)
  and `offset`; the UI pages in batches of 20.
- `POST /api/attendance/{attendance_id}/feedback` accepts `{rating, feedback_text?}`.
  Ratings must be integers 1–5; text is trimmed and limited to 2,000 characters. Only the
  owning attendee can submit, using a valid session and the configured web Origin.
- The API rechecks event eligibility, including rescheduled dates. Unknown or another
  user's attendance returns 404; a no-longer-eligible request returns 409. Responses are
  `no-store` and never enter the public event cache.
- Replaying identical feedback preserves `feedback_at`. Editing changes the feedback and
  its timestamp without changing invite history or attendance identity.
- The existing graph rebuild emits ATTENDED with rating, text, and native datetime
  `feedback_at`, replacing ATTENDING from canonical state. INVITED_TO is preserved.
  Postgres/API changes are immediate; graph changes appear on the next projection run.

Migration `20260928_0017` adds the request queue, a database rating constraint, and the
explicit global-audit exception for this DAG. Downgrade refuses to erase or mislabel
feedback-job audit history; those records must be handled explicitly before downgrading.

## Verification

From `api/`: `pytest`, `ruff check .`, `mypy`. Tests cover owner-only access, rating/text
validation, idempotent submission, saved history, graph projection, and migration
upgrade/downgrade with forbidden duplicate and out-of-range writes.

From `airflow/`: `pytest tests/test_feedback.py tests/test_feedback_dag.py tests/test_graph.py
tests/test_graph_dag.py`, then `ruff check .` and `mypy`. Tests cover timezone/DST boundaries,
catch-up, shadow claim eligibility, rollback, retries, and two real Airflow DAG executions.

From `web/`: `npm test`, `npm run lint`, `npm run build`. From `api/`, run
`python tests/run_auth_browser.py` for sign-in, invites, and feedback browser flows against
disposable Postgres. Google responses are replayed locally; the feedback job uses the
real persistence path. Browser tests verify saved feedback across reloads and private
cache exclusion, with desktop/mobile screenshots.
