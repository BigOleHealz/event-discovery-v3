# In-app invites (6b)

Sign in, open an event, choose matched contacts or enter up to 20 registered account emails,
add an optional message, and send. The account's **Invitations** button opens Received and Sent lists. Recipients
can accept or decline, and senders can refresh Sent to see the response. Invite/account
responses use credentialed requests and `no-store`; private data never enters the public
map cache. Signing out removes the inbox and event invite form.

This sub-phase routes to existing, non-shadow Google accounts. Unknown emails reject the
whole batch; no partial send occurs. Contact selection is available in 6d. Share links with
signed-in acceptance are available in [6d.1](share-invites.md); map attendance badges belong
to 6e. Provider-sent SMS and social shadow accounts are deferred beyond v1.

## API

- `POST /api/invites`: `{canonical_event_id, emails?: [...], contact_ids?: [...], message?: string}`. Emails are
  validated and deduplicated after case normalization. Contact ids must belong to the
  sender and match registered users; an unmatched contact rejects the entire batch.
  Emails and contacts are deduplicated by recipient. The combined limit is 20 selections.
  Unknown fields (including the former `phones` field) are rejected. Only future, unarchived events
  accept new invitations. The authenticated user is the sender; clients cannot choose it.
- `GET /api/invites/received`: only rows addressed to the authenticated user.
- `GET /api/invites/sent`: only rows whose `invited_by` array contains that user.
- Both lists accept `limit` (1–100, default 50) and `offset` (default 0), ordered by send
  time and ID. Responses omit email, phone, and Google identity fields.
- `POST /api/invites/{id}/respond`: `{response: "accept" | "decline"}`. Only the recipient
  can respond. An unknown or unauthorized invite returns 404. Mutations require the
  exact configured web Origin as well as a valid session.

## Persistence and decisions

Postgres remains authoritative. Its existing partial unique index enforces one invite per
registered person/event pair. Atomic `ON CONFLICT` appends a new sender once; retries by
the same sender are idempotent. A repeat invite preserves status, response time, original
message, first send time, and channel. The schema has only one message, so the first
message wins; it is not represented as a message from each subsequent sender.

Acceptance locks the invite and writes `attending` / `invite_accept` attendance in the
same transaction. It keeps the invite row as provenance. Repeating the same response
changes neither attendance nor response time. Before the event starts, recipients may
change their response: declining removes only upcoming attendance created by invite
acceptance, preserving independent RSVPs and historical feedback. After start/archive,
changing a response returns 409; retrying an already-recorded response remains harmless.

The existing `project_to_neo4j` DAG now also projects minimal User nodes, one INVITED_TO
edge per registered invitee/event with the sender list, and ATTENDING from attendance.
It rebuilds all nodes and edges atomically from its repeatable-read Postgres snapshot;
retries and recovery preserve both attendance and invite provenance. Personal email,
phone, Google subject, and invite message are excluded from the graph. API responses
reflect changes immediately; Neo4j catches up on the next existing scheduled/manual
rebuild (hourly by default). API mutations remain available during a graph outage.

No schema migration was needed: the initial migration already owns invite, attendance,
and their unique indexes. Existing dedup review deliberately refuses to delete events
with dependent user data; this behavior is unchanged.

## Verification

From `api/`, run `pytest tests/test_invites.py`. Tests use real Postgres/Neo4j, including
simultaneous senders, a forbidden duplicate write, recipient-only access, transactional
rollback, and repeated/drift-repair graph rebuilds. From `airflow/`, run
`pytest tests/test_graph.py tests/test_graph_dag.py`.

From `api/`, `python tests/run_auth_browser.py` runs both auth and two-browser invite
flows using the local Google replay and a disposable database, with no live delivery.
From `web/`, run `npm test`, `npm run lint`, and `npm run build`.
