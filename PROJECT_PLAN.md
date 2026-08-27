# Event Discovery Platform — Project Plan

## 1. Purpose

A map-first event discovery app. The home page is a Google Map showing events scraped
from across the web, filterable by date, time, and category, with Google sign-in and the
ability to invite friends to an event.

Beyond the product itself, this project exists as a **stable domain and stack to
experiment in**. The domain is broad enough (scraping, geospatial, graph, vector search,
scheduling, auth, real-time UI) to justify swapping in and trying out new technologies
without having to learn an unfamiliar problem space at the same time.

**In-person events only.** Online/virtual events are filtered out at ingest and never
stored as discoverable events — the entire premise is a map, and an online event has no
meaningful location. See §5 for where the filter sits.

The frontend is a **progressive web app** — installable to a phone home screen from the
browser, with an offline shell, so there is one codebase for web and mobile. The service
worker this requires is the same one Web Push needs in Phase 7e.

Non-goals for v1: native iOS/Android builds (the PWA covers mobile), ticketing/payments,
user-generated events, production-grade scaling.

---

## 2. Architecture Overview

```
                    ┌──────────────────────────┐
                    │  Frontend (React)        │
                    │  Google Maps + sidebar   │
                    └───────────┬──────────────┘
                                │ REST/JSON
                    ┌───────────▼──────────────┐
                    │  API (FastAPI)           │
                    │  auth, search, invites   │
                    └──┬────────┬────────┬─────┘
                       │        │        │
        ┌──────────────▼──┐ ┌───▼─────┐ ┌▼──────────────┐
        │ Postgres+PostGIS│ │ Neo4j   │ │ Qdrant        │
        │ canonical data  │ │ graph   │ │ dedup vectors │
        └──────────▲──────┘ └───▲─────┘ └▲──────────────┘
                   │            │        │
                ┌──┴────────────┴────────┴──┐
                │  Airflow (ingestion DAGs)  │
                │  scrapers + Stagehand      │
                └──────────┬─────────────────┘
                           │
                ┌──────────▼─────────────────┐
                │ Ingestion metadata DB      │
                │ (separate Postgres schema) │
                └────────────────────────────┘
```

### Services

| Service | Role |
|---|---|
| `web` | React frontend (PWA: manifest + service worker), Google Maps JS API |
| `api` | FastAPI — search, filters, auth, invites |
| `airflow` | Scheduler + workers for ingestion DAGs |
| `postgres` | Canonical event/user/invite data (PostGIS enabled) |
| `neo4j` | Event ↔ category ↔ venue ↔ source relationships |
| `qdrant` | Embeddings for cross-source dedup |
| `redis` | Airflow broker + API response cache |
| `stagehand` | Browser automation worker for JS-rendered sites |

---

## 3. Data Model

### 3.1 Postgres (canonical)

```sql
-- Venues first: canonical_event references them.
-- Table order in this section is dependency order, so the whole block runs top-to-bottom.
CREATE TABLE venue (
    id              UUID PRIMARY KEY,
    name            TEXT,
    formatted_address TEXT,
    google_place_id TEXT UNIQUE,
    location        GEOGRAPHY(POINT, 4326),
    city            TEXT,
    region          TEXT,
    country         TEXT
);

-- One row per real-world event, regardless of how many sites list it
CREATE TABLE canonical_event (
    id              UUID PRIMARY KEY,
    title           TEXT NOT NULL,
    description     TEXT,
    starts_at       TIMESTAMPTZ NOT NULL,
    ends_at         TIMESTAMPTZ,
    timezone        TEXT NOT NULL,
    venue_id        UUID REFERENCES venue(id),
    location        GEOGRAPHY(POINT, 4326) NOT NULL,
    primary_category TEXT,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    archived_at     TIMESTAMPTZ            -- set 30 days after end; hidden from map, still queryable
);

-- No is_online column by design: online events are rejected at ingest (see §5),
-- so every row here has a real physical location. location is NOT NULL for that reason.

CREATE INDEX ON canonical_event USING GIST (location);
CREATE INDEX ON canonical_event (starts_at);

-- One row per listing found on a source site. Multiple rows -> one canonical_event.
CREATE TABLE source_listing (
    id                  UUID PRIMARY KEY,
    canonical_event_id  UUID REFERENCES canonical_event(id),
    source              TEXT NOT NULL,          -- 'meetup' | 'eventbrite' | 'phillyfunguide' | ...
    source_event_id     TEXT NOT NULL,
    url                 TEXT NOT NULL,
    registration_url    TEXT,
    price_min           NUMERIC,
    price_max           NUMERIC,
    raw_payload         JSONB NOT NULL,
    ingestion_run_id    UUID NOT NULL,
    first_seen_at       TIMESTAMPTZ DEFAULT now(),
    last_seen_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source, source_event_id)
);

CREATE TABLE app_user (
    id              UUID PRIMARY KEY,
    google_sub      TEXT UNIQUE,           -- null for shadow accounts
    email           TEXT UNIQUE,
    phone_e164      TEXT UNIQUE,
    display_name    TEXT,
    avatar_url      TEXT,
    is_shadow       BOOLEAN DEFAULT FALSE, -- created by an accepted invite, never signed in
    claimed_at      TIMESTAMPTZ,           -- set when a shadow account completes OAuth
    created_at      TIMESTAMPTZ DEFAULT now(),
    CHECK (google_sub IS NOT NULL OR is_shadow)
);

-- Imported phone/Google contacts, so invites can go out over SMS
CREATE TABLE contact (
    id              UUID PRIMARY KEY,
    owner_user_id   UUID REFERENCES app_user(id),
    display_name    TEXT,
    phone_e164      TEXT,
    email           TEXT,
    matched_user_id UUID REFERENCES app_user(id),   -- set when contact is a registered user
    source          TEXT,                            -- google_contacts|manual|device
    imported_at     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON contact (owner_user_id);

-- Mirrors the Neo4j INVITED_TO edge: one row per (invitee, event)
CREATE TABLE invite (
    id                  UUID PRIMARY KEY,
    canonical_event_id  UUID REFERENCES canonical_event(id),
    to_user_id          UUID REFERENCES app_user(id),
    to_contact_id       UUID REFERENCES contact(id),   -- non-users invited by text/email
    invited_by          UUID[] NOT NULL,               -- appends on repeat invites
    status              TEXT DEFAULT 'pending',        -- pending|accepted|declined
    channel             TEXT,                          -- sms|email|in_app
    message             TEXT,
    sent_at             TIMESTAMPTZ DEFAULT now(),
    responded_at        TIMESTAMPTZ,
    CHECK (to_user_id IS NOT NULL OR to_contact_id IS NOT NULL)
);

CREATE UNIQUE INDEX ON invite (canonical_event_id, to_user_id)
    WHERE to_user_id IS NOT NULL;

CREATE TABLE attendance (
    id                  UUID PRIMARY KEY,
    canonical_event_id  UUID REFERENCES canonical_event(id),
    user_id             UUID REFERENCES app_user(id),
    state               TEXT NOT NULL,      -- attending|attended|no_show
    source              TEXT,               -- invite_accept|self_rsvp
    rating              SMALLINT,           -- 1-5, post-event feedback
    feedback_text       TEXT,
    feedback_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (canonical_event_id, user_id)
);

-- Persisted filter sets for notifications
CREATE TABLE saved_search (
    id              UUID PRIMARY KEY,
    user_id         UUID REFERENCES app_user(id),
    name            TEXT NOT NULL,
    bounds          GEOGRAPHY(POLYGON, 4326) NOT NULL,
    categories      TEXT[],
    day_of_week     SMALLINT[],         -- null = any day
    time_of_day_start TIME,
    time_of_day_end   TIME,
    notify_channel  TEXT DEFAULT 'email',   -- email|sms|none
    notify_frequency TEXT DEFAULT 'daily',  -- instant|daily|weekly
    last_notified_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE push_subscription (
    id              UUID PRIMARY KEY,
    user_id         UUID REFERENCES app_user(id),
    endpoint        TEXT NOT NULL,      -- Web Push endpoint or FCM token
    keys            JSONB,              -- p256dh + auth for Web Push
    platform        TEXT,               -- web|ios|android
    home_location   GEOGRAPHY(POINT, 4326),
    radius_meters   INT DEFAULT 25000,
    quiet_hours_start TIME,
    quiet_hours_end   TIME,
    max_per_week    SMALLINT DEFAULT 3,
    created_at      TIMESTAMPTZ DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);

-- Every push sent, for frequency capping and click-through measurement
CREATE TABLE notification_log (
    id                  UUID PRIMARY KEY,
    user_id             UUID REFERENCES app_user(id),
    canonical_event_id  UUID REFERENCES canonical_event(id),
    channel             TEXT NOT NULL,   -- push|email|sms
    trigger             TEXT NOT NULL,   -- saved_search|nearby|invite|feedback_request
    sent_at             TIMESTAMPTZ DEFAULT now(),
    opened_at           TIMESTAMPTZ,
    UNIQUE (user_id, canonical_event_id, trigger)
);

-- Which events have already been sent for a saved search, so we never re-notify
CREATE TABLE saved_search_hit (
    saved_search_id     UUID REFERENCES saved_search(id),
    canonical_event_id  UUID REFERENCES canonical_event(id),
    notified_at         TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (saved_search_id, canonical_event_id)
);
```

**Key point:** both source listings are kept. The UI shows one pin, and lets the user pick
which platform to register through.

### 3.2 Ingestion metadata DB

Separate schema (`ingest`) so operational data never mixes with product data.

```sql
CREATE TABLE ingest.run (
    id              UUID PRIMARY KEY,
    run_date        DATE NOT NULL,
    source          TEXT NOT NULL,
    source_url      TEXT,
    city_searched   TEXT NOT NULL,
    search_bounds   GEOGRAPHY(POLYGON, 4326),
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL,      -- running|success|partial|failed
    events_found    INT DEFAULT 0,
    events_new      INT DEFAULT 0,
    events_updated  INT DEFAULT 0,
    events_deduped  INT DEFAULT 0,
    events_rejected_online INT DEFAULT 0,   -- filtered as virtual/online
    error_message   TEXT,
    airflow_dag_id  TEXT,
    airflow_run_id  TEXT
);

CREATE TABLE ingest.page_fetch (
    id              UUID PRIMARY KEY,
    run_id          UUID REFERENCES ingest.run(id),
    url             TEXT NOT NULL,
    http_status     INT,
    fetch_method    TEXT,               -- 'http' | 'stagehand'
    duration_ms     INT,
    bytes           INT,
    error_message   TEXT,
    fetched_at      TIMESTAMPTZ DEFAULT now()
);

-- Listings dropped before reaching canonical_event, kept for filter tuning.
-- Mostly online events; also malformed or ungeocodable listings.
CREATE TABLE ingest.rejected_listing (
    id              UUID PRIMARY KEY,
    run_id          UUID REFERENCES ingest.run(id),
    source          TEXT NOT NULL,
    source_event_id TEXT,
    url             TEXT,
    reason          TEXT NOT NULL,   -- online|no_location|ungeocodable|malformed|past
    raw_payload     JSONB,
    rejected_at     TIMESTAMPTZ DEFAULT now()
);
```

This gives per-day, per-source, per-city observability: what ran, what it found, what
broke, and how long it took.

### 3.3 Neo4j (relationships)

Nodes: `CanonicalEvent`, `SourceListing`, `Category`, `Venue`, `User`, `Contact`, `City`

Relationships:
```
(:SourceListing)-[:LISTS]->(:CanonicalEvent)
(:CanonicalEvent)-[:HAS_CATEGORY]->(:Category)
(:Category)-[:SUBCATEGORY_OF]->(:Category)
(:CanonicalEvent)-[:HELD_AT]->(:Venue)
(:Venue)-[:IN_CITY]->(:City)
(:CanonicalEvent)-[:SIMILAR_TO {score}]->(:CanonicalEvent)

(:User)-[:INVITED_TO]->(:CanonicalEvent)
(:User)-[:ATTENDING]->(:CanonicalEvent)
(:User)-[:ATTENDED]->(:CanonicalEvent)
(:User)-[:FRIENDS_WITH]-(:User)
(:User)-[:HAS_CONTACT]->(:Contact)
(:Contact)-[:IS_USER]->(:User)
```

#### Invite model

The invite edge belongs to the **invitee**, pointing at the event, with the senders
recorded as a list property:

```cypher
(:User {id: invitee})-[:INVITED_TO {
    invited_by:  ["user-uuid-1", "user-uuid-2"],  // appends on repeat invites
    status:      "pending",                        // pending | accepted | declined
    channel:     "sms",                            // sms | email | in_app
    sent_at:     datetime(),
    responded_at: null
}]->(:CanonicalEvent {id: event})
```

One edge per person-event pair. Two friends inviting you to the same show appends to
`invited_by` rather than creating a parallel edge.

On acceptance: flip `status` to `accepted`, set `responded_at`, and create
`(:User)-[:ATTENDING]->(:CanonicalEvent)`. The `INVITED_TO` edge is kept — it's the
provenance of how they found the event. After the event passes, `ATTENDING` is rewritten to
`ATTENDED` with the feedback properties (see Phase 6c).

**Known tradeoffs of this model**, accepted for v1:

- Only one `sent_at` / `channel` per edge, so per-inviter timestamps are lost when multiple
  people invite the same person. Storing index-aligned parallel arrays would fix it but is
  brittle.
- Sender-side queries ("did my invite get accepted?") require scanning invitees' edges and
  filtering on `invited_by` list membership, rather than walking out from the sender.

If those queries start to hurt, reify to `(:User)-[:SENT]->(:Invite)-[:TO]->(:User)` with
`(:Invite)-[:FOR_EVENT]->(:CanonicalEvent)`. Cheap to change later — Neo4j is a projection
rebuilt from Postgres, so it's a rebuild, not a data migration.

#### Contacts

`Contact` nodes come from imported phone/Google contacts and hold name plus phone/email.
When a contact's email or phone matches a registered `app_user`, link `IS_USER` so invites
route in-app instead of over SMS. This is what makes text invites work without requiring the
recipient to have an account.

Postgres is the system of record; Neo4j is a projection, rebuilt from Postgres if it drifts.
It powers category hierarchies ("show me all music, including subgenres"), venue history,
the friends-are-going map layer, and later on recommendations.

### 3.4 Qdrant (dedup)

Single collection `event_listings`. **No per-city or per-date collections** — metro
boundaries (Minneapolis/Saint Paul) and midnight-spanning events break that partitioning,
and it produces thousands of tiny collections.

```python
PointStruct(
    id=listing_uuid,
    vector=embed(f"{title}\n{description[:500]}"),
    payload={
        "starts_at_epoch": 1735689600,
        "lat": 39.9526, "lon": -75.1652,
        "location": {"lat": 39.9526, "lon": -75.1652},  # geo payload index
        "source": "eventbrite",
        "venue_name": "World Cafe Live",
        "city": "Philadelphia",   # metadata label only, never a partition key
    },
)
```

---

## 4. Deduplication Strategy

Vector similarity alone will happily match two different open-mic nights at the same bar
on different Tuesdays. So: **hard filters first, similarity second.**

```
Step 1 — Hard filter (Qdrant payload filter)
   • starts_at within ± 90 minutes of candidate
   • geo_radius: 500m of candidate coordinates

Step 2 — Vector similarity on the survivors
   • cosine similarity on title + description embedding
   • score >= 0.88  -> same event
   • 0.75 – 0.88    -> queue for manual review
   • < 0.75         -> distinct event

Step 3 — Resolution
   • match found    -> attach source_listing to existing canonical_event
   • no match       -> create new canonical_event, attach listing
   • either way     -> upsert point into Qdrant, write SIMILAR_TO edge in Neo4j
```

Exact-match shortcut: if two listings share a `google_place_id` **and** a start time to the
minute **and** a normalized title, skip the vector step entirely.

Merge conflicts (differing titles/descriptions across sources) resolve by source priority,
configurable — currently Eventbrite > Meetup > scraped sites.

### Manual review queue

The 0.75–0.88 band gets a **real review UI**, not just a SQL view. Judgement calls in that
band are frequent enough that reviewing them in a database client is the thing that stops
getting done.

```sql
CREATE TABLE dedup_review (
    id                  UUID PRIMARY KEY,
    listing_a_id        UUID REFERENCES source_listing(id),
    listing_b_id        UUID REFERENCES source_listing(id),
    similarity_score    NUMERIC NOT NULL,
    time_delta_minutes  INT,
    distance_meters     INT,
    status              TEXT DEFAULT 'pending',  -- pending|merged|distinct|skipped
    decided_by          UUID REFERENCES app_user(id),
    decided_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE (listing_a_id, listing_b_id)
);
```

Admin-only route at `/admin/dedup`: side-by-side pair view showing both titles,
descriptions, venues, times, and source URLs, with Merge / Distinct / Skip. Keyboard
shortcuts, since this is a queue you work through in batches.

Decisions feed back as labelled data — once there are a few hundred, they justify tuning the
0.88 and 0.75 thresholds against actual precision and recall rather than intuition. Pairs
left pending stay as separate canonical events, so an unreviewed queue degrades to duplicate
pins, never to wrongly merged events.

---

## 5. Ingestion Pipeline

### Airflow DAGs

| DAG | Schedule | Notes |
|---|---|---|
| `ingest_meetup` | daily 03:00 | Official API where available |
| `ingest_eventbrite` | daily 03:15 | Official API |
| `ingest_site_generic` | daily 03:30 | HTTP + parser per site config |
| `ingest_site_stagehand` | daily 04:00 | JS-rendered sites (e.g. Philly listings) |
| `geocode_pending` | hourly | Resolve addresses → coordinates |
| `dedup_pending` | hourly | Run the matching pipeline |
| `project_to_neo4j` | hourly | Rebuild graph projection |
| `archive_past_events` | daily 05:00 | Flag `archived_at` on events ended 30+ days ago |
| `notify_saved_searches` | hourly | Match new events against saved searches, send digests |
| `request_event_feedback` | daily 10:00 | Prompt attendees of events that ended yesterday |
| `match_contacts_to_users` | daily 02:00 | Link `Contact` → `User` on new signups |
| `push_nearby_events` | daily 09:00 | Proximity + interest push notifications (see §7) |

Each DAG run opens an `ingest.run` row on start and closes it on finish, success or fail.

### Task flow per source

```
open_run  →  fetch_pages  →  parse_listings  →  geocode  →  dedup  →  upsert  →  close_run
```

Scraping should stage raw payloads in `source_listing.raw_payload` before parsing, so a
parser bug is re-runnable without re-fetching.

### Stagehand

Used for sites that render listings client-side or hide them behind interaction. Runs as
its own container so browser dependencies stay out of the Airflow image. Airflow tasks call
it over HTTP and get structured JSON back.

### Online-event filtering

In-person only, so online events are dropped in `parse_listings`, before geocoding — no
point spending Google API calls on events that get discarded. Layered checks, cheapest
first:

1. **Source flags** — Meetup exposes `is_online_event`, Eventbrite `online_event`. Trust
   these when present; they catch the majority.
2. **Venue heuristics** — venue name or address matching `online`, `virtual`, `webinar`,
   `zoom`, `google meet`, `teams`, `livestream`, `remote`, or empty/placeholder addresses.
3. **Title/description keywords** — only as a tiebreaker when there's no usable venue, since
   "livestreamed" often just means an in-person gig that also streams. A real address plus a
   stream mention stays in.
4. **Geocode failure** — if there's no resolvable physical address after step 3, reject as
   `no_location`.

Every rejection writes an `ingest.rejected_listing` row with its reason, so the filter can
be tuned against real misses instead of guesswork. Hybrid events (real venue *and* a stream)
count as in-person and are kept.

### Retention

**Past events are retained, not deleted.** They're the substrate for attendance history,
post-event feedback, and eventually recommendations — and they cost almost nothing to keep.

- `canonical_event` rows persist indefinitely
- `prune_past_events` is replaced by `archive_past_events`: events whose `ends_at` passed
  more than 30 days ago get `archived_at` set, which excludes them from map queries by
  default while leaving them queryable
- Map and search endpoints filter to `starts_at >= now()` unless an explicit past date range
  is requested
- Partition `canonical_event` by month on `starts_at` if the table gets uncomfortable — a
  later optimization, not a v1 concern

### Geocoding

Scraped addresses are strings, not coordinates. Google Geocoding/Places API resolves them
to lat/lon plus a `google_place_id`, which doubles as the venue dedup key. Cache
aggressively in the `venue` table — the same venues recur constantly and the API bills
per call.

---

## 6. Search API

### Primary endpoint

```
GET /api/events?
    north=40.05&south=39.88&east=-75.09&west=-75.28   # viewport bounds
    &starts_after=2026-09-01T00:00:00Z
    &starts_before=2026-09-07T23:59:59Z
    &categories=music,comedy
    &zoom=13
```

Behaviour by zoom level:

- **zoom ≥ 13** — return individual events (capped at ~500) as GeoJSON points
- **zoom < 13** — return aggregated grid cells: `{lat, lon, count, top_categories}`

Server-side aggregation uses PostGIS `ST_SnapToGrid` at a cell size derived from zoom.
Zooming in resolves cells into real markers.

Other endpoints:

```
GET  /api/events/{id}          # detail incl. all source listings + registration URLs
GET  /api/events/{id}.ics      # calendar export
GET  /api/categories           # hierarchy from Neo4j
POST /api/auth/google          # OAuth callback → session cookie
GET  /api/me

POST /api/invites              # send invite(s); appends to invited_by if edge exists
GET  /api/invites/received
POST /api/invites/{id}/respond # accept | decline → creates ATTENDING on accept
GET  /api/invites/sent         # list-membership scan on invited_by

POST /api/contacts/import      # Google Contacts / vCard upload
GET  /api/contacts             # incl. matched_user_id for in-app routing

GET  /api/saved-searches
POST /api/saved-searches
PATCH/DELETE /api/saved-searches/{id}

GET  /api/events/friends       # friends-are-going layer, viewport-scoped
POST /api/attendance/{id}/feedback   # rating + free text, post-event

POST /api/push/subscribe       # Web Push subscription + home location/radius
PATCH /api/push/preferences    # quiet hours, weekly cap, radius
DELETE /api/push/subscribe

GET  /api/admin/dedup/queue    # 0.75-0.88 review pairs
POST /api/admin/dedup/{id}     # merged | distinct | skipped
```

All event-returning endpoints exclude `archived_at IS NOT NULL` and filter to
`starts_at >= now()` unless an explicit past range is passed.

---

## 7. Frontend

- **Map**: Google Maps JS API, full-bleed on the home page
- **Refetch**: on map `idle` event, debounced ~300ms so panning doesn't hammer the backend
- **Clustering**: marker clustering for dense areas; server-provided cell counts below
  zoom 13
- **Sidebar filter**: date range picker, time-of-day range, category multi-select — all
  filters are URL query params so views are shareable
- **Event detail**: slide-over panel; shows one event with a registration button per source
  ("Register on Eventbrite" / "Register on Meetup")
- **Auth**: Google OAuth sign-in button; signed-in state unlocks the invite flow
- **Invite flow**: pick event → select from imported contacts or enter emails/numbers →
  optional message → send (routes in-app when the contact matches a registered user, SMS or
  email otherwise)
- **Friends layer**: toggle on the map that restyles pins your friends are `ATTENDING`,
  with their avatars in the event detail panel
- **Saved searches**: "save this search" button on the sidebar filter, capturing the current
  bounds + filters; managed from a settings panel with per-search notification frequency
- **Calendar export**: "Add to calendar" on the event detail — ICS download plus a Google
  Calendar deep link
- **Post-event prompt**: for past `ATTENDING` events, an inline card asking "did you go?
  how was it?" — 1-5 rating plus optional text
- **Push notifications**: Web Push (service worker) for proximity- and interest-based
  suggestions, with an opt-in prompt shown only after a user has RSVP'd at least once — never
  on first visit
- **Notification settings**: home location, radius, quiet hours, and a weekly cap, all
  user-editable

### Progressive web app

Installable from the browser on desktop and mobile — no app store, one codebase.

- **Manifest** — name, icons at all required sizes, `display: standalone`, theme colour,
  `start_url` at the map
- **Service worker** — precache the app shell (JS, CSS, icons, map container) so a cold
  launch renders instantly; the same worker handles Web Push in Phase 7e
- **Offline behaviour** — cache the last successful `/api/events` response per viewport and
  serve it stale with an "offline, showing last known events" banner. Event detail for cached
  events works offline; search and invites require the network and say so.
- **Install prompt** — listen for `beforeinstallprompt`, show a dismissible "add to home
  screen" affordance after the second visit, never on first load
- **iOS caveats** — Safari only allows Web Push for installed PWAs, so the push opt-in in
  Phase 7e must detect `display-mode: standalone` and prompt the user to install first
- **Runtime caching** — network-first for event data, cache-first for tiles and static
  assets, with a versioned cache name so deploys invalidate cleanly

---

### Push notification strategy

Proximity push is the easiest feature here to make annoying, so the constraints are part of
the design rather than a later fix:

- **Frequency cap** — `max_per_week`, default 3, hard-enforced against `notification_log`
- **Quiet hours** — per-user, defaulting to 21:00–09:00 local
- **Relevance floor** — only push events matching a category the user has RSVP'd to before,
  or that a friend is `ATTENDING`. No interest signal means no push.
- **Proximity** — within `radius_meters` of `home_location` (default 25km)
- **Lead time** — 2 to 7 days out, so there's still time to make plans
- **Never repeat** — the unique constraint on `(user_id, canonical_event_id, trigger)` makes
  duplicate sends impossible at the database level
- **Batching** — one digest push with several events beats several pushes
- **Auto-backoff** — if `opened_at` stays null across the last several sends, halve the
  user's rate; this is measurable from `notification_log` alone

`push_nearby_events` runs daily; the friends-are-going variant can fire sooner since it's
inherently higher signal.

### Shadow accounts

When someone accepts an SMS invite without an account, create a **shadow `app_user`**:
`is_shadow = true`, `google_sub` null, identified by `phone_e164`. This means invite
acceptance works in one tap with no signup wall, and the person gets a real `ATTENDING`
edge — so they appear in their friends' map layer immediately.

On later OAuth signup, match on phone or email, set `claimed_at`, populate `google_sub`, and
flip `is_shadow` to false. Attendance and invite history carry over untouched, because they
already point at that user id.

Constraints worth respecting: shadow accounts can't sign in, can't send invites, and can't
receive push (no subscription without a session). They exist to hold attendance state. Merge
on claim needs care if a shadow account and a real account both exist for the same
person — match on phone first, then email, and prefer the older id as the survivor.

## 8. Testing Strategy

Tests are written **alongside each sub-phase**, not deferred to a later phase. A sub-phase
is not done until its tests pass. These test real behaviour against real dependencies —
containers, not mocks — wherever the dependency is cheap to run.

### Layers

| Layer | Tool | What it covers |
|---|---|---|
| Unit | `pytest` | Parsers, dedup scoring, online-event filter, geocode normalization |
| Database | `pytest` + `testcontainers` | PostGIS bounding-box and `ST_SnapToGrid` queries, constraints, migrations |
| API | `pytest` + `httpx.AsyncClient` | Every endpoint: params, auth, pagination, error shapes |
| DAG | `pytest` + Airflow test harness | Task wiring, retries, `ingest.run` lifecycle |
| Frontend unit | `vitest` + Testing Library | Sidebar filters, URL param sync, detail panel |
| E2E | `playwright` | Map loads, filter changes results, sign-in, invite flow, PWA install |
| Migrations | `alembic` upgrade/downgrade | Every revision applies and reverses on a clean DB |

### Rules that matter

- **Real Postgres, always.** PostGIS behaviour is the thing under test; SQLite or a mocked
  session tests nothing useful. `testcontainers` spins up `postgis/postgis:16-3.4` per session.
- **Saved payloads as fixtures.** Every scraper commits real captured `raw_payload` JSON to
  `tests/fixtures/<source>/`. Parser tests run against those — no live network in the suite.
  When a site changes its markup, add the new payload as a fixture and watch the old test fail.
- **Dedup gets a labelled corpus.** `tests/fixtures/dedup/` holds hand-labelled pairs: known
  duplicates across sources, and the nasty negatives (same venue, same title, different
  Tuesday). Assert on classification, not on raw scores, so threshold tuning doesn't break
  the suite. Decisions from `/admin/dedup` graduate into this corpus over time.
- **Time is injected, never `now()`.** Anything touching `starts_at`, archival, quiet hours,
  or notification lead time takes a clock parameter. Freeze it in tests.
- **External APIs are recorded.** Google Geocoding, Meetup, Eventbrite, Twilio: record once
  with `vcr.py` (or hand-written fixtures), replay in CI. No test spends a real API call.
- **Constraints are tested as behaviour.** The unique index on
  `(user_id, canonical_event_id, trigger)` has a test that tries to double-send and asserts
  the insert fails — that constraint is the anti-spam guarantee, so it needs a test proving it.
- **Idempotency.** Every DAG task runs twice in a test and asserts the second run produces no
  duplicate rows. Ingestion is inherently re-run.
- **Service worker.** Playwright asserts the manifest is served, the worker registers, the
  shell loads with the network offline, and the stale-data banner appears.

### CI

GitHub Actions on every push: lint (`ruff`, `eslint`), type check (`mypy`, `tsc`), then the
suite. Postgres and Qdrant as service containers. E2E runs on PRs only, since it's the slow
one. Coverage reported but not gated on a number — a threshold just invites tests written to
satisfy it.

---

## 9. Docker Compose

Everything runs locally under one `docker-compose.yml`, one container per service.

```yaml
services:
  web:        { build: ./web,      ports: ["3000:3000"] }
  api:        { build: ./api,      ports: ["8000:8000"] }
  airflow:    { build: ./airflow }
  stagehand:  { build: ./stagehand }
  postgres:   { image: postgis/postgis:16-3.4, volumes: [pgdata:/var/lib/postgresql/data] }
  neo4j:      { image: neo4j:5,    volumes: [neo4jdata:/data] }
  qdrant:     { image: qdrant/qdrant, volumes: [qdrantdata:/qdrant/storage] }
  redis:      { image: redis:7 }

volumes: { pgdata: {}, neo4jdata: {}, qdrantdata: {} }
```

### Twelve-factor rules from day one

So this lifts to the cloud without a rewrite:

1. **All config via environment variables** — no config files baked into images
2. **No hardcoded service hostnames** — `POSTGRES_HOST`, `NEO4J_URI`, `QDRANT_URL` etc.,
   defaulted to Compose service names but always overridable
3. **Named volumes for all stateful services** — never state inside a container layer
4. **Stateless API containers** — sessions in Redis or signed cookies, not in memory
5. **Secrets from env, never committed** — `.env.example` checked in, `.env` gitignored
6. **One process per container**
7. **Health checks on every service** so orchestrators can manage them

---

## 10. Deployment Path

Target: a cheap provider rather than full AWS. Railway, Render, and Fly all read a Compose
file reasonably well.

Cost consideration to plan for early: **managed Neo4j and Qdrant are the expensive pieces.**
Realistic v1 shape is app containers on the PaaS, Postgres managed (cheap and available
everywhere), and Neo4j + Qdrant self-hosted together on a small VPS. Worth pricing before
committing, since it affects whether the graph and vector layers stay separate services or
get folded into Postgres extensions (`pgvector`, recursive CTEs) as a fallback.

---

## 11. Roadmap

Each phase is shippable on its own. Sub-phases are the natural commit boundaries — no
sub-phase should leave the app broken.

### Phase 1 — Skeleton

- **1a** — Compose file with `postgres` (PostGIS), `api`, `web`; health checks on all three;
  `.env.example` committed
- **1b** — Postgres schema migrations (`canonical_event`, `venue`, `source_listing`) via
  Alembic; seed script with ~20 hand-written Philly events
- **1c** — FastAPI `GET /api/events` returning seeded events as GeoJSON
- **1d** — React app with a full-bleed Google Map rendering the seeded pins
- **1e** — PWA shell: manifest, icon set, service worker precaching the app shell,
  installable and passing a Lighthouse PWA audit

*Done when:* hardcoded events appear on a real map, all from containers, and the app
installs to a phone home screen.

### Phase 2 — One source, end to end

- **2a** — Airflow container joins Compose; `ingest.run` / `ingest.page_fetch` schema
- **2b** — `ingest_eventbrite` DAG: fetch → stage `raw_payload` → parse
- **2b.1** — Online-event filter in `parse_listings`, with `ingest.rejected_listing` logging
- **2c** — `geocode_pending` DAG: Google Geocoding → `venue` rows keyed on
  `google_place_id`, cached
- **2d** — Upsert into `canonical_event` + `source_listing` (one canonical per listing for
  now, no dedup)
- **2e** — Map shows real scraped events; event detail slide-over with registration link

*Done when:* a nightly DAG run puts genuine Eventbrite events on the map.

### Phase 3 — Search and filters

- **3a** — Bounding-box query on `GET /api/events` with PostGIS GIST index
- **3b** — Debounced (~300ms) refetch on map `idle`
- **3c** — Zoom-dependent response: individual pins at zoom ≥ 13, `ST_SnapToGrid`
  aggregated cells below
- **3d** — Client-side marker clustering for dense areas
- **3e** — Sidebar: date range, time-of-day, category multi-select — all as URL query params
- **3f** — Offline caching of the last successful viewport response, with a stale-data banner

*Done when:* zooming out over the northeast returns counts, not thirty thousand pins.

### Phase 4 — Multi-source and deduplication

- **4a** — `ingest_meetup` DAG; two sources now producing overlapping events
- **4b** — Qdrant container; `event_listings` collection with geo + `starts_at_epoch`
  payload indexes
- **4c** — Exact-match shortcut: `google_place_id` + start-minute + normalized title
- **4d** — `dedup_pending` DAG: hard filter (±90min, 500m) → cosine similarity → resolve
- **4e** — Canonical/source split in the UI: one pin, a registration button per source
- **4f** — `dedup_review` table + admin review UI at `/admin/dedup`: side-by-side pairs,
  Merge / Distinct / Skip, keyboard shortcuts

*Done when:* the same show listed on both platforms is one pin with two register buttons,
and the ambiguous band is reviewable in a browser.

### Phase 5 — Graph layer

- **5a** — Neo4j container; `project_to_neo4j` DAG rebuilding the projection from Postgres
- **5b** — Category hierarchy with `SUBCATEGORY_OF`; hierarchical filtering in the sidebar
  ("music" includes subgenres)
- **5c** — `SIMILAR_TO` edges written from dedup scores; "similar events" in the detail panel

*Done when:* filtering on a parent category returns children, resolved in Cypher.

### Phase 6 — Auth and social

- **6a** — Google OAuth, `app_user`, session cookies, stateless API containers
- **6b** — Invites: `INVITED_TO` edge with `invited_by` list, accept/decline flow,
  `ATTENDING` on acceptance
- **6c** — Post-event feedback: `request_event_feedback` DAG, `ATTENDING` → `ATTENDED`
  rewrite, rating + text capture
- **6d** — Contacts import (Google Contacts / vCard), `match_contacts_to_users` DAG,
  SMS invites for unmatched contacts (Twilio; per-message cost absorbed for now)
- **6d.1** — Shadow accounts: one-tap SMS invite acceptance, claim-on-signup merge
- **6e** — Friends: `FRIENDS_WITH` edges, and the friends-are-going map layer

*Done when:* you can text a friend an invite and see them light up on the map on accept.

### Phase 7 — Retention features

- **7a** — Calendar export: ICS endpoint + Google Calendar deep link
- **7b** — Saved searches: persist bounds + filters, management UI
- **7c** — `notify_saved_searches` DAG with `saved_search_hit` dedup so nothing sends twice
- **7d** — Notification delivery (email first, SMS second) with instant/daily/weekly
  frequencies
- **7e** — Web Push on the existing service worker, `push_subscription`, opt-in prompt gated
  behind a first RSVP; on iOS, detect non-installed state and prompt to install first
- **7f** — `push_nearby_events` DAG with the full anti-spam stack: relevance floor, weekly
  cap, quiet hours, `notification_log` dedup, open-rate backoff

*Done when:* a genuinely relevant event two towns over pushes to your phone, and you don't
resent it.

### Phase 8 — Hard sources

- **8a** — Stagehand container with an HTTP interface, browser deps isolated from Airflow
- **8b** — Per-site parser configs; `ingest_site_stagehand` DAG
- **8c** — Philly-style JS-rendered sites onboarded
- **8d** — Per-source rate limiting and ToS review before each site goes live

*Done when:* a site with no API and no server-rendered listings is ingesting nightly.

### Phase 9 — Deploy

- **9a** — Price the target provider; decide managed vs. self-hosted for Neo4j and Qdrant
- **9b** — Managed Postgres, app containers on the PaaS, secrets from provider env
- **9c** — Domain, TLS, OAuth redirect URIs for production
- **9d** — Backups for Postgres; documented rebuild path for Neo4j and Qdrant (both are
  projections, so restore = re-run the DAG)

*Done when:* it's on the internet and the nightly ingestion runs without you watching.

### Deliberately deferred

- **Recommendations.** Tempting once the graph exists, but without attendance history it
  can't do better than "more comedy, since you liked comedy." Revisit after Phase 6c has
  accumulated real `ATTENDED` data.
- **User-generated events**, ticketing/payments, native mobile apps.

## 12. Experimentation Backlog

The point of the project — a known domain to try unfamiliar tools in. Candidates:

- **Ingestion**: swap Airflow for Dagster or Prefect and compare
- **Streaming**: Kafka between scrape and dedup instead of direct DB writes
- **Search**: Typesense or Meilisearch for text search alongside geo
- **Embeddings**: compare local models vs. hosted; try reranking on the dedup middle band
- **API**: GraphQL layer over the REST core
- **Frontend**: swap Google Maps for MapLibre + self-hosted tiles (also unlocks true offline
  maps, since tiles could be cached by the service worker)
- **Vector**: compare Qdrant against pgvector on the same dedup workload
- **Infra**: Terraform the deployment; try Nomad or k3s
- **Observability**: OpenTelemetry tracing across ingestion → API → frontend
- **LLM**: category classification and description summarization at ingest time

---

## 13. Decisions Made

Settled, recorded so they don't get relitigated:

- **In-person only.** Online events filtered at ingest, rejections logged for filter tuning.
  Hybrid events count as in-person. (§5)
- **Retain past events.** No hard delete; `archived_at` hides them from the map 30 days after
  they end, but they stay queryable as history. (§5)
- **SMS via Twilio**, per-message cost absorbed for now. Revisit if invite volume grows. (§6d)
- **Manual review UI for the 0.75–0.88 dedup band** — a real admin screen, not a SQL view,
  with decisions kept as labelled data for later threshold tuning. (§4)
- **Shadow accounts** for non-users who accept SMS invites, claimed on later signup. (§7)
- **Push notifications** for relevant nearby events, with frequency caps, quiet hours, a
  relevance floor, and open-rate backoff built in from the start. (§7)
- **Progressive web app, not native.** Installable from the browser on desktop and mobile,
  one codebase; the service worker doubles as the Web Push worker. Native builds are a
  non-goal for v1. (§1, §7)

## 14. Open Questions

1. Recurring events — one canonical event with a recurrence rule, or one per occurrence?
   (Affects the dedup time-window logic significantly.)
2. Timezone handling for events near timezone boundaries — store venue timezone on `venue`
   and derive, or resolve per event at ingest?
3. Rate limits and ToS review for each scraped source before adding it.
4. Shadow account merge edge case: same person with both a shadow account (phone) and a real
   account (email), no overlapping identifier. Detectable at all, or accept the duplicate?
5. Push relevance floor needs RSVP history to work, but new users have none — is a cold-start
   signal worth it (declared category interests at signup), or do new users simply get no
   push until they RSVP once?
6. Does the friends-are-going layer need privacy controls in v1, or is "friends only" scoping
   sufficient?
