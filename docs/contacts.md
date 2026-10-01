# Contacts and in-app invitations — Phase 6d

Apply the API migrations before starting the API or Airflow. Contact data lives in Postgres.
API containers do not retain Google access tokens between requests.

## Setup

- Enable Google's People API for the OAuth project. Register
  `GOOGLE_CONTACTS_REDIRECT_URI` as an additional authorized redirect URI. Set
  `GOOGLE_PEOPLE_CONNECTIONS_URL` to the connections endpoint shown in `.env.example`.
  Importing contacts asks for `contacts.readonly` separately from sign-in, binds consent
  to the signed-in account and browser with a ten-minute signed state/PKCE cookie, reads
  all pages, and discards the access token. No refresh token is requested or stored.
- `CONTACT_MATCH_DAG_SCHEDULE` defaults to `0 2 * * *`. The existing graph projection
  schedule also projects `Contact`, `HAS_CONTACT`, and `IS_USER` from canonical data.
  Contact names/addresses in Neo4j are internal data, never a public contacts endpoint.

Provider reference: [Google connections API](https://developers.google.com/people/api/rest/v1/people.connections/list).

## Using contacts

Open **Contacts** in the account menu to import Google Contacts or upload a UTF-8 vCard.
Imports accept up to 2,000 contact addresses and vCards up to 1 MB. Numbers without an
explicit country code default to +1 (US/Canada and other +1 regions). For example,
`(415) 555-2671`, `1 (415) 555-2671`, and `+1 (415) 555-2671` all become `+14155552671`.
Explicit international codes are preserved; for other countries, include `+` and the
country code. Include the area code. Invalid numbers and extensions are rejected.
Emails are normalized to lowercase. A failed import saves no partial results.
Contacts with no phone/email are skipped.

For iPhone/iCloud contacts:

1. Open **Contacts → Lists** on the iPhone.
2. Press and hold the desired iCloud list and choose **Export**.
3. Include names, phone numbers, and email addresses, then tap **Done**.
4. Choose **Save to Files**, then upload that `.vcf` using **Import vCard (.vcf)** in the app.

Alternatively, use Contacts on iCloud.com from a tablet or computer, select the desired
contacts, and choose **Share → Export vCard**. See
[Apple’s iPhone export instructions](https://support.apple.com/guide/iphone/export-contacts-iph075ddebf2/ios)
and [iCloud export instructions](https://support.apple.com/guide/icloud/import-export-and-print-contacts-mmfba748b2/icloud).
Importing copies contact data; it does not continuously sync with iCloud. Export and
import again to update contacts. Export only names, phones, and emails to keep the file
small; photos and other fields are not stored by this importer.

Each phone is stored separately, carrying the first email; additional emails become
separate rows. Only rows matched to registered users are selectable for invitations.
Reimporting the same phone (or email for email-only contacts) for the same owner updates
its existing row, preserving its id and invitation references.
The same address can appear in different owners' private address books.

Matching prefers an exact E.164 phone, then an email, and excludes shadow/unregistered
accounts. Imports and Google signups run the same database matching function as the
audited, retry-safe `match_contacts_to_users` DAG. The DAG also clears stale matches.
These links identify contacts; they do not grant authentication or merge account ids.

On an event, choose matched contacts or enter comma-separated registered account emails.
Up to 20 addresses/contact selections can be submitted together. Recipients are deduplicated
by user id, so an email and a contact for the same person create one in-app invitation.
Unknown emails, unmatched contacts, and self-invites reject the entire batch. Contact ids
belonging to another user return 404. Unmatched contacts remain in the address book and
show **No matching account**. Use **Share invite** on the event, then choose the friend
in your messaging app; no contact identity is bound to the link.
Direct phone entry is no longer supported.

All private reads are scoped to the current session; cookie-authenticated writes require
the configured web origin. Responses use `no-store`, and the service worker does not
cache contacts or invitations.

## Sharing and historical records

The app does not send SMS, call Twilio, or offer delivery retries. No Twilio credentials
are needed. [Share links](share-invites.md) with Google sign-in and explicit acceptance
are available in **6d.1**. Social shadow accounts and claim-on-signup merges
are deferred beyond v1; the provisional dedup-review administrator remains unchanged.

Committed migration 0018 remains intact, including its historical `sms_delivery` table
and contact-invite unique index. Existing SMS invitations remain visible to their senders
as past invitations, with no retry controls or delivery status claims. No existing invite,
attendance, contact, or delivery records are deleted or rewritten by this revision.
The API no longer reads or writes the delivery table. Existing pending rows are inert.

## Verification

API tests use real Postgres/Neo4j with hand-written Google People HTTP replays, covering
owner isolation, constraints, concurrent senders, atomic rejection of unmatched contacts,
preserved legacy records, matching, and graph repair.
Airflow's real DAG harness runs matching twice. `api/tests/run_auth_browser.py` exercises
Google consent, vCard upload, matched contact selection, an in-app invitation, and recipient
acceptance through the browser and real API/database, while retaining the auth/invite/feedback
regressions.
