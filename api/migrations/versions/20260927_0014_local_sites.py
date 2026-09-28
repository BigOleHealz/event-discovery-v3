"""Onboard local calendars and their required access policies (Phase 8c)."""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0014"
down_revision: str | None = "20260927_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHILADELPHIA = "8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed"
BALTIMORE = "bf2d365e-2eb6-4eed-a112-662c7b435893"
FIELDS = (
    "source_event_id",
    "url",
    "title",
    "description",
    "starts_at",
    "ends_at",
    "venue_name",
    "venue_address",
    "venue_city",
    "venue_region",
    "venue_country",
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {field: {"type": ["string", "null"]} for field in FIELDS},
    "required": ["source_event_id", "url", "title", "starts_at", "venue_address"],
}
SOURCES = [
    {
        "source": "reads-and-company",
        "method": "stagehand",
        "priority": 10,
        "market": PHILADELPHIA,
        "target": "29b13ff2-2e65-4450-b01e-c9694051af30",
        "url": "https://www.readsandcompany.com/events",
        "ready": "#maintable h2",
        "venue": {
            "venue_name": "Reads & Company",
            "venue_address": "234 Bridge Street",
            "venue_city": "Phoenixville",
            "venue_region": "PA",
            "venue_country": "US",
        },
        "filter": {
            "include_all": ["book group"],
            "exclude_any": ["Colonial", "Bistro", "online", "virtual"],
        },
        "instruction": "Read event rows under maintable. Extract the heading's direct title text "
        "and the start of its em date range, stripping ordinal day suffixes. "
        "Only in-store book groups are in scope; apply the configured card_filter. "
        "The verified venue defaults apply only to this bounded in-store scope. "
        "Do not extract book synopses as event descriptions. Leave description and ends_at null. "
        "There are no native DOM event IDs or links: leave source_event_id and url null "
        "for the configured deterministic identity and listing URL fallback.",
        "notes": "Rendered calendar and footer reviewed 2026-09-27; no dedicated ToS link found. "
        "robots.txt only restricts dotbot /i/, not this listing. Public in-store book-group "
        "facts and source links only; exclude offsite events and book descriptions. "
        "No documented public event API found. HTTP has an empty React root.",
    },
    {
        "source": "philamoca",
        "method": "http",
        "priority": 11,
        "market": PHILADELPHIA,
        "target": "682cd0a5-95f8-40d1-9f55-c2cf51d95026",
        "url": "https://www.philamoca.org/",
        "ready": None,
        "venue": {
            "venue_name": "PhilaMOCA",
            "venue_address": "531 N. 12th St.",
            "venue_city": "Philadelphia",
            "venue_region": "PA",
            "venue_country": "US",
        },
        "filter": {"exclude_any": ["off-site", "offsite", "RUBA Club", "private party"]},
        "instruction": "Read public upcoming event cards with links. Ignore Just Announced. "
        "Combine event date's datetime attribute with the Show time datetime attribute, "
        "not Doors. Use the native outbound event link as url. Links can repeat for series; "
        "leave source_event_id null to use title/start/venue identity. Apply card_filter "
        "to exclude private and offsite events. Use verified in-venue defaults. "
        "Leave description and ends_at null; do not infer missing show times.",
        "notes": "Public calendar/footer reviewed 2026-09-27; no dedicated ToS link found. "
        "robots.txt allows the listing and specifies Crawl-delay: 10, enforced by the "
        "10-second minimum interval. Event facts and outbound links only, no posters "
        "or prose. Listings are server-rendered, so ordinary HTTP is sufficient.",
    },
    {
        "source": "charm-city-books",
        "method": "stagehand",
        "priority": 12,
        "market": BALTIMORE,
        "target": "e9f5b119-40fb-44fd-ae1a-2e809a8ac63b",
        "url": "https://www.charmcitybooks.com/events",
        "ready": "#main-content h4",
        "venue": {
            "venue_name": "Charm City Books",
            "venue_address": "426 West Franklin St.",
            "venue_city": "Baltimore",
            "venue_region": "MD",
            "venue_country": "US",
        },
        "filter": {"include_all": ["The Bookshop!"], "exclude_any": ["online", "virtual"]},
        "instruction": "Read event rows containing an h4 title. Only rows explicitly located "
        "at The Bookshop! are in scope; apply card_filter and verified venue defaults. "
        "Combine the ordinal calendar date and first time from the following horizontal "
        "metadata row. Ignore the end time. Leave description, ends_at and venue_name null. "
        "No native DOM event IDs or links are available, so leave source_event_id and url "
        "null for configured deterministic identity and listing URL fallback.",
        "notes": "Rendered calendar/footer reviewed 2026-09-27; no dedicated ToS link found. "
        "robots.txt only restricts dotbot /i/, not this listing. Public event facts and "
        "source links only, explicitly limited to The Bookshop! venue; offsite and "
        "virtual events excluded. No documented public event API found. HTTP has an "
        "empty React root. Conservative 10-second minimum between page requests.",
    },
]


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ingest.source_adapter
            ADD COLUMN access_policy JSONB,
            ADD COLUMN min_request_interval_seconds INTEGER
                CHECK (min_request_interval_seconds BETWEEN 1 AND 3600),
            ADD COLUMN next_fetch_at TIMESTAMPTZ,
            ADD CONSTRAINT access_policy_shape CHECK (
                access_policy IS NULL OR coalesce((
                    jsonb_typeof(access_policy) = 'object'
                    AND access_policy ?&
                        ARRAY['status','reviewed_at','listing_urls','references','notes']
                    AND access_policy->>'status' IN ('reviewed','blocked','pending')
                    AND jsonb_typeof(access_policy->'reviewed_at') = 'string'
                    AND length(access_policy->>'reviewed_at') > 0
                    AND jsonb_typeof(access_policy->'listing_urls') = 'array'
                    AND jsonb_array_length(access_policy->'listing_urls') > 0
                    AND jsonb_typeof(access_policy->'references') = 'array'
                    AND jsonb_array_length(access_policy->'references') > 0
                    AND jsonb_typeof(access_policy->'notes') = 'string'
                    AND length(access_policy->>'notes') > 0
                    AND min_request_interval_seconds IS NOT NULL
                ), false)
            );
    """)
    connection = op.get_bind()
    connection.execute(
        sa.text("""
        INSERT INTO ingest.market(id,slug,name,city,region,country_code,timezone)
        VALUES (:id,'baltimore-md','Baltimore','Baltimore','MD','US','America/New_York')
        ON CONFLICT (id) DO NOTHING
    """),
        {"id": BALTIMORE},
    )
    for source in SOURCES:
        extraction = {
            "schema": SCHEMA,
            "instruction": source["instruction"],
            "venue": source["venue"],
            "card_filter": source["filter"],
            "identity": "title_start_venue",
            "listing_url_fallback": True,
        }
        if source["ready"]:
            extraction["ready_selector"] = source["ready"]
        url = str(source["url"])
        origin = url.split("/", 3)[:3]
        policy = {
            "status": "reviewed",
            "reviewed_at": "2026-09-27T12:00:00Z",
            "listing_urls": [url],
            "references": [url, "/".join(origin) + "/robots.txt"],
            "notes": source["notes"],
        }
        if source["method"] == "stagehand":
            policy["references"].append("https://bookmanager.com/privacy-policy")
        connection.execute(
            sa.text("""
            INSERT INTO ingest.source_adapter
                (source,fetch_method,priority,extraction,pagination,model,prompt_version,
                 enabled,access_policy,min_request_interval_seconds)
            VALUES (:source,:method,:priority,CAST(:extraction AS jsonb),'{"kind":"none"}',
                    'gpt-5.4-2026-03-05',1,true,CAST(:policy AS jsonb),10)
        """),
            {**source, "extraction": json.dumps(extraction), "policy": json.dumps(policy)},
        )
        connection.execute(
            sa.text("""
            INSERT INTO ingest.crawl_target
                (id,source,market_id,source_location,category,enabled,window_days,page_cap)
            VALUES (:target,:source,:market,CAST(:location AS jsonb),'arts',true,30,1)
        """),
            {**source, "location": json.dumps({"kind": "listing_url", "url": url})},
        )


def downgrade() -> None:
    connection = op.get_bind()
    for source in SOURCES:
        connection.execute(sa.text("DELETE FROM ingest.crawl_target WHERE id=:target"), source)
        connection.execute(
            sa.text("DELETE FROM ingest.source_adapter WHERE source=:source"), source
        )
    # Preserve the market if operational runs or additional targets now reference it.
    connection.execute(
        sa.text("""
        DELETE FROM ingest.market WHERE id=:id
        AND NOT EXISTS (SELECT 1 FROM ingest.run WHERE market_id=:id)
        AND NOT EXISTS (SELECT 1 FROM ingest.crawl_target WHERE market_id=:id)
    """),
        {"id": BALTIMORE},
    )
    op.execute("""
        ALTER TABLE ingest.source_adapter DROP CONSTRAINT access_policy_shape,
            DROP COLUMN next_fetch_at, DROP COLUMN min_request_interval_seconds,
            DROP COLUMN access_policy;
    """)
