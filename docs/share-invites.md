# Share invitations — Phase 6d.1

Apply API migrations through 0019. Sign in, open an event, and choose **Share invite**.
Add an optional message and create a link. **Share link** opens the device's native share
sheet where supported; **Copy link** and a selectable URL provide fallbacks. The separate
share button preserves the user gesture required by mobile share sheets after link creation.
Choose the messaging app and recipient yourself. No provider sends SMS or email.

Links can be forwarded or sent to a group. Opening one shows a limited preview. Recipients
sign in with Google and return to the invitation, then explicitly choose **Accept invitation**.
Sign-in, preview, share cancellation, and copying never create an invite or attendance.
The preview's event-details link opens another tab so the invitation stays available.

In **Invitations → Share links**, the creator sees creation time, the first observed open,
acceptance count, and accepting users' display names/times. Opens may include previews or
bots; neither opens nor use of the share sheet proves delivery. Tracking is private to
the creator. Each page contains 20 links and displays up to the first 100 accepting users
per link, with the full count. Creators can revoke a link. Existing acceptances remain
recorded after revocation; revocation prevents subsequent acceptance.

## Tokens and expiry

Each token has 256 bits of cryptographic randomness. Postgres stores only its SHA-256
hash. The raw URL is returned once on creation; tracking cannot recover it. Save/copy
the URL before closing the event panel, or create another link later.

The shared URL uses a fragment, which is not sent to the web server or in HTTP referrers.
The frontend immediately removes the fragment from visible history and keeps the pending
token in tab-scoped sessionStorage across Google OAuth. It is never included in Google's
authorization parameters or an arbitrary return URL. Closing or accepting the invitation
clears pending storage. Server-side expiry/revocation checks remain authoritative.
Auth sessions continue to use signed cookies, without API-container session memory.

API previews/acceptance use the token routes below. The ASGI middleware redacts those
paths in Uvicorn access logs while routing against a private scope copy. Responses use
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`; the web document also sets
no-referrer, and the service worker never caches API responses. Any future reverse proxy
or request tracing must likewise omit/redact `/api/invite-links/*` URLs and response bodies.

`INVITE_LINK_TTL_SECONDS` defaults to 604800 (seven days), accepts 60–2592000 seconds,
and is capped at the event start time. Expired/revoked links and past/archived events cannot
be accepted, including retries of previous acceptances. A creator cannot accept their own link.

## API and persistence

- `POST /api/invite-links`: signed-in creator, trusted Origin, event id and optional message.
  Returns id, expiry, and share URL.
- `GET /api/invite-links`: creator's tracking only, with limit/offset pagination.
- `GET /api/invite-links/{token}`: limited public preview; records first observed open only.
- `POST /api/invite-links/{token}/accept`: Google session, trusted Origin, explicit acceptance.
- `POST /api/invite-links/{id}/revoke`: creator only, trusted Origin, idempotent revocation.

Acceptance locks the link and event, atomically upserts the recipient/event invite,
appends the creator once, records accepted status, inserts attendance if absent, and
inserts one acceptance per link/user. Multiple creators preserve one person/event invite.
The original invite message, channel, and send time survive; an already accepted invite
keeps its response time. Independent attendance and feedback are preserved. Existing
in-app decline/accept controls remain available; link tracking records the acceptance
action, while the invite inbox shows the current RSVP.

Links do not verify phone/email ownership, attach the recipient to an imported contact,
merge accounts, or create friendships. No social shadow accounts are created. The existing
provisional dedup-review administrator remains unchanged.

The existing graph rebuild projects the resulting INVITED_TO and ATTENDING relationships;
share-link bearer tokens and tracking tables are not projected. Foreign keys prevent
deleting an event with share history, so existing dedup merges roll back for those events.
No extra dedup data-movement policy is introduced here.

## Verification

Run API share-link and migration tests against disposable Postgres/Neo4j; they cover
privacy, expiry, revocation, atomic rollback, concurrent/multiple-recipient acceptance,
unique constraints, and repeat graph rebuilds. The browser harness
`api/tests/run_auth_browser.py` exercises sharing cancellation, copying, public preview,
replayed Google signup, return to the pending invitation, explicit acceptance, creator
tracking and revocation. Google and map responses are replayed; no SMS provider is used.
Native share-sheet behavior is covered through a browser replay, not a physical iPhone.
