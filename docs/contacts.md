# Contacts and SMS invitations — Phase 6d

Apply the API migrations before starting the API or Airflow. All contact data and SMS
attempts live in Postgres. API containers retain neither Google access tokens nor
delivery state in memory between requests.

## Setup

- Enable Google's People API for the OAuth project. Register
  `GOOGLE_CONTACTS_REDIRECT_URI` as an additional authorized redirect URI. Set
  `GOOGLE_PEOPLE_CONNECTIONS_URL` to the connections endpoint shown in `.env.example`.
  Importing contacts asks for `contacts.readonly` separately from sign-in, binds consent
  to the signed-in account and browser with a ten-minute signed state/PKCE cookie, reads
  all pages, and discards the access token. No refresh token is requested or stored.
- Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, and
  `TWILIO_MESSAGES_URL` (the Messages endpoint for that same account). The sender must
  be configured to send SMS in the Twilio account. No application setting enables live
  provider calls in the test suite; tests inject HTTP replays.
- `CONTACT_MATCH_DAG_SCHEDULE` defaults to `0 2 * * *`. The existing graph projection
  schedule also projects `Contact`, `HAS_CONTACT`, and `IS_USER` from canonical data.
  Contact names/addresses in Neo4j are internal data, never a public contacts endpoint.

Provider references: [Google connections API](https://developers.google.com/people/api/rest/v1/people.connections/list),
[Twilio Messages API](https://www.twilio.com/docs/messaging/api/message-resource).

## Using contacts

Open **Contacts** in the account menu to import Google Contacts or upload a UTF-8 vCard.
Imports accept up to 2,000 contact addresses and vCards up to 1 MB. Phone numbers must
include an international country code; extensions and local-only numbers are rejected
rather than assigned a guessed country. Emails are normalized to lowercase. A failed
import saves no partial results. Contacts with no phone/email are skipped.

Each phone is selectable separately, carrying the first email; additional emails become
separate selectable rows. Reimporting the same phone (or email for email-only contacts)
for the same owner updates its existing row, preserving its id and invitation references.
The same address can appear in different owners' private address books.

Matching prefers an exact E.164 phone, then an email, and excludes shadow/unregistered
accounts. Imports and Google signups run the same database matching function as the
audited, retry-safe `match_contacts_to_users` DAG. The DAG also clears stale matches.
These links identify contacts; they do not grant authentication or merge account ids.

On an event, choose contacts or enter comma-separated registered emails/international
phone numbers. Up to 20 recipients can be invited together. Matched users receive the
existing in-app invite; unmatched phone contacts receive SMS. Unmatched email-only
contacts cannot be sent an invitation yet. Direct phone entry also saves a manual contact.
All private reads are scoped to the current session; cookie-authenticated writes require
the configured web origin. Responses use `no-store`, and the service worker does not
cache contacts or invitations.

## Delivery and retries

An invitation and its SMS body/number are committed before contacting Twilio. A unique
outbox row and an atomic persisted `pending → sending` claim prevent duplicate submissions
from repeated/concurrent requests. A successful Twilio submission is shown in **Sent**;
it is not a carrier delivery receipt. Carrier callbacks are not implemented in this step.

Known rejections show **Retry SMS**. Pending work left before a submission can also be
retried. A timeout, server failure, malformed success response, or process crash after
claiming leaves delivery uncertain (`unknown` or `sending`); it is never retried
automatically. Operators must reconcile those attempts with Twilio before changing their
state. This deliberately avoids claiming exactly-once delivery across an external API.

SMS links open a public event detail panel through `?event=<event UUID>`. Shadow accounts,
one-tap SMS acceptance, and claiming/merging existing SMS invite history belong to **6d.1**.
This step preserves unmatched invitations against their contact rows for that work.

## Verification

API tests use real Postgres/Neo4j with hand-written People/Twilio HTTP replays, covering
owner isolation, constraints, concurrent submissions, failures, matching, and graph repair.
Airflow's real DAG harness runs matching twice. `api/tests/run_auth_browser.py` exercises
Google consent, vCard upload, contact selection, SMS submission, and the event link through
the browser and real API/database, while retaining the auth/invite/feedback regressions.
