"""Add reviewed Philadelphia calendars and structured extraction diagnostics (Phase 8)."""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0016"
down_revision: str | None = "20260928_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PHILADELPHIA = "8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed"
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
SOURCES = [
    {
        "source": "grid-magazine",
        "method": "http",
        "url": "https://gridphilly.com/events/",
        "target": "1868a75f-3758-48cb-bdb9-c1b6c9068dca",
        "category": "community",
        "venue": {},
        "references": [],
        "notes": "Public calendar and footer reviewed 2026-09-28. Robots has an empty "
        "Disallow for all agents. No dedicated terms or privacy link found in reviewed "
        "calendar/footer. Scope: factual event metadata and source links only; no prose "
        "or images. Exclude online, cancelled, untimed and addressless entries. "
        "Includes Greater Philadelphia venues; existing geocoding locates each venue.",
    },
    {
        "source": "bartrams-garden",
        "method": "stagehand",
        "url": "https://www.bartramsgarden.org/calendar/",
        "target": "027d0118-866d-41cf-a6b4-af2f3c9c0ef4",
        "category": "community",
        "venue": {},
        "references": [],
        "notes": "Public calendar/footer and robots reviewed 2026-09-28. Empty Disallow; "
        "honor the published 10-second Crawl-delay. No dedicated terms link found. "
        "Factual event metadata and links only. Use each event's explicit street "
        "address, including offsite venues; never assume all events occur at the garden.",
    },
    {
        "source": "fleisher",
        "method": "stagehand",
        "url": "https://fleisher.org/calendar/",
        "target": "4d6c4279-53de-42da-96db-1f3e541f1f59",
        "category": "arts",
        "venue": {
            "venue_name": "Fleisher Art Memorial",
            "venue_address": "719 Catharine Street",
            "venue_city": "Philadelphia",
            "venue_region": "PA",
            "venue_country": "US",
        },
        "references": ["https://fleisher.org/about-us/privacy-policy/"],
        "notes": "Calendar, linked privacy policy and robots reviewed 2026-09-28. Empty "
        "Disallow; no scraping prohibition found in linked policy. Public event facts "
        "and links only. Calendar explicitly states all events occur at 719 Catharine "
        "Street unless otherwise noted: defaults apply only when metadata has no "
        "location; never override explicit offsite locations. Exclude midnight/all-day "
        "term entries and closure notices rather than invent event times.",
    },
]


def upgrade() -> None:
    op.execute("""ALTER TABLE ingest.site_page ADD COLUMN extraction_skips JSONB NOT NULL
        DEFAULT '[]'::jsonb CHECK (jsonb_typeof(extraction_skips) = 'array')""")
    connection = op.get_bind()
    for index, source in enumerate(SOURCES):
        url = str(source["url"])
        policy = {
            "status": "reviewed",
            "reviewed_at": "2026-09-28T12:00:00Z",
            "listing_urls": [url, url + "list/", url + "list/page/2/"],
            "references": [
                url,
                "/".join(url.split("/")[:3]) + "/robots.txt",
                *source["references"],
            ],
            "notes": source["notes"] + " Two-page nightly cap; 10-second minimum interval. "
            "Operational review is not a license or written permission. "
            + (
                "Local HTTP returned a Cloudflare challenge; ordinary Stagehand navigation "
                "returned HTTP 200 with public metadata and no challenge interaction."
                if source["method"] == "stagehand"
                else "Ordinary HTTP verified live."
            ),
        }
        extraction = {
            "format": "jsonld",
            "card_filter": {
                "exclude_any": [
                    "canceled",
                    "cancelled",
                    "building closure",
                    "no adult classes",
                    "online",
                    "virtual",
                ]
            },
            "exclude_midnight": True,
            "venue": source["venue"],
            "instruction": "Map public schema.org Event facts and event URLs. No descriptions "
            "or images. Use explicit start/end timestamps and location fields. "
            "Find the enabled next-page link. Keep all cities data-driven.",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {field: {"type": ["string", "null"]} for field in FIELDS},
                "required": ["source_event_id", "url", "title", "starts_at", "venue_address"],
            },
        }
        connection.execute(
            sa.text("""
            INSERT INTO ingest.source_adapter
              (source,fetch_method,priority,extraction,pagination,model,prompt_version,
               enabled,access_policy,min_request_interval_seconds)
            VALUES (:source,:method,:priority,CAST(:extraction AS jsonb),
                    '{"kind":"next_link"}','gpt-5.4-2026-03-05',1,true,CAST(:policy AS jsonb),10)
        """),
            {
                "source": source["source"],
                "priority": 20 + index,
                "method": source["method"],
                "extraction": json.dumps(extraction),
                "policy": json.dumps(policy),
            },
        )
        connection.execute(
            sa.text("""
            INSERT INTO ingest.crawl_target
              (id,source,market_id,source_location,category,enabled,window_days,page_cap)
            VALUES (:target,:source,:market,CAST(:location AS jsonb),:category,true,30,2)
        """),
            {
                **source,
                "market": PHILADELPHIA,
                "location": json.dumps({"kind": "listing_url", "url": url}),
            },
        )


def downgrade() -> None:
    connection = op.get_bind()
    for source in SOURCES:
        connection.execute(sa.text("DELETE FROM ingest.crawl_target WHERE id=:target"), source)
        connection.execute(
            sa.text("DELETE FROM ingest.source_adapter WHERE source=:source"), source
        )
    op.execute("ALTER TABLE ingest.site_page DROP COLUMN extraction_skips")
