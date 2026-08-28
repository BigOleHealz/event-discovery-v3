"""Create or update one canonical event for each geocoded source listing."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import psycopg

from ingestion.clock import Clock
from ingestion.geocoding import normalize_address
from ingestion.models import ParsedListing
from ingestion.sources import parse_source_listing

CANONICAL_EVENT_NAMESPACE = uuid.UUID("d3c81426-26c5-49b9-bf16-c1d13aabedcb")
WriteAction = Literal["created", "updated", "unchanged"]


@dataclass(frozen=True)
class CanonicalizationCandidate:
    """A staged listing whose latest source payload has not been canonicalized."""

    listing_id: uuid.UUID
    ingestion_run_id: uuid.UUID
    source: str
    listing: ParsedListing
    normalized_address: str


@dataclass(frozen=True)
class CanonicalizationSummary:
    """Observable counts from one canonicalization pass."""

    candidates: int
    created: int
    updated: int
    unchanged: int
    awaiting_geocode: int


class CanonicalEventRepository:
    """Persist one canonical event per source listing without cross-listing deduplication."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def candidates(self) -> tuple[CanonicalizationCandidate, ...]:
        """Return new or freshly re-scraped supported source listings."""
        with psycopg.connect(self._database_url) as connection:
            rows = connection.execute(
                """
                SELECT sl.id, sl.ingestion_run_id, sl.source, sl.raw_payload,
                       market.timezone
                FROM source_listing AS sl
                JOIN ingest.run AS run ON run.id = sl.ingestion_run_id
                JOIN ingest.market AS market ON market.id = run.market_id
                LEFT JOIN canonical_event AS event ON event.id = sl.canonical_event_id
                WHERE sl.source IN ('eventbrite', 'meetup')
                  AND (
                    sl.canonical_event_id IS NULL
                    OR event.updated_at IS NULL
                    OR sl.last_seen_at > event.updated_at
                  )
                ORDER BY sl.source, sl.source_event_id
                """
            ).fetchall()

        candidates: list[CanonicalizationCandidate] = []
        for listing_id, ingestion_run_id, source, raw_payload, market_timezone in rows:
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            if not isinstance(raw_payload, dict):
                raise TypeError("source_listing.raw_payload must be a JSON object")
            if not isinstance(source, str) or not isinstance(market_timezone, str):
                raise TypeError("source and market timezone must be text")
            listing = parse_source_listing(
                source,
                cast(dict[str, object], raw_payload),
                market_timezone=market_timezone,
            )
            if listing.venue_address is None:
                continue
            if not isinstance(listing_id, uuid.UUID) or not isinstance(ingestion_run_id, uuid.UUID):
                raise TypeError("source_listing identifiers must be UUID values")
            candidates.append(
                CanonicalizationCandidate(
                    listing_id=listing_id,
                    ingestion_run_id=ingestion_run_id,
                    source=source,
                    listing=listing,
                    normalized_address=normalize_address(listing.venue_address),
                )
            )
        return tuple(candidates)

    def cached_venue_id(self, normalized_address: str) -> uuid.UUID | None:
        """Resolve the venue already paid for by the geocoding step."""
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT venue_id
                FROM ingest.geocode_cache
                WHERE normalized_address = %(normalized_address)s
                """,
                {"normalized_address": normalized_address},
            ).fetchone()
        if row is None:
            return None
        venue_id = row[0]
        if not isinstance(venue_id, uuid.UUID):
            raise TypeError("geocode cache returned a non-UUID venue id")
        return venue_id

    def upsert(
        self,
        *,
        candidate: CanonicalizationCandidate,
        venue_id: uuid.UUID,
        written_at: datetime,
    ) -> WriteAction:
        """Atomically upsert the canonical row, link its listing, and count the action."""
        with psycopg.connect(self._database_url) as connection:
            locked = connection.execute(
                """
                SELECT sl.canonical_event_id, sl.last_seen_at, event.updated_at
                FROM source_listing AS sl
                LEFT JOIN canonical_event AS event ON event.id = sl.canonical_event_id
                WHERE sl.id = %(listing_id)s
                FOR UPDATE OF sl
                """,
                {"listing_id": candidate.listing_id},
            ).fetchone()
            if locked is None:
                raise LookupError(f"source_listing {candidate.listing_id} does not exist")
            existing_event_id, last_seen_at, prior_updated_at = locked
            if existing_event_id is not None and not isinstance(existing_event_id, uuid.UUID):
                raise TypeError("source_listing canonical_event_id must be a UUID")
            if not isinstance(last_seen_at, datetime):
                raise TypeError("source_listing last_seen_at must be a datetime")
            if prior_updated_at is not None and not isinstance(prior_updated_at, datetime):
                raise TypeError("canonical_event updated_at must be a datetime")
            if (
                existing_event_id is not None
                and prior_updated_at is not None
                and last_seen_at <= prior_updated_at
            ):
                return "unchanged"

            event_id = existing_event_id or uuid.uuid5(
                CANONICAL_EVENT_NAMESPACE,
                f"{candidate.source}:{candidate.listing.source_event_id}",
            )
            row = connection.execute(
                """
                INSERT INTO canonical_event (
                    id, title, description, starts_at, ends_at, timezone,
                    venue_id, location, primary_category, created_at, updated_at,
                    archived_at
                )
                SELECT
                    %(event_id)s, %(title)s, %(description)s, %(starts_at)s,
                    %(ends_at)s, %(timezone)s, venue.id, venue.location,
                    %(primary_category)s, %(written_at)s, %(written_at)s, NULL
                FROM venue
                WHERE venue.id = %(venue_id)s AND venue.location IS NOT NULL
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    starts_at = EXCLUDED.starts_at,
                    ends_at = EXCLUDED.ends_at,
                    timezone = EXCLUDED.timezone,
                    venue_id = EXCLUDED.venue_id,
                    location = EXCLUDED.location,
                    primary_category = EXCLUDED.primary_category,
                    updated_at = EXCLUDED.updated_at,
                    archived_at = NULL
                RETURNING id
                """,
                {
                    "event_id": event_id,
                    "title": candidate.listing.title,
                    "description": candidate.listing.description,
                    "starts_at": candidate.listing.starts_at,
                    "ends_at": candidate.listing.ends_at,
                    "timezone": candidate.listing.timezone,
                    "venue_id": venue_id,
                    "primary_category": candidate.listing.primary_category,
                    "written_at": written_at,
                },
            ).fetchone()
            if row is None:
                raise LookupError(f"venue {venue_id} has no geocoded location")

            connection.execute(
                """
                UPDATE source_listing
                SET canonical_event_id = %(event_id)s,
                    url = %(url)s,
                    registration_url = %(registration_url)s
                WHERE id = %(listing_id)s
                """,
                {
                    "event_id": event_id,
                    "url": candidate.listing.url,
                    "registration_url": candidate.listing.url,
                    "listing_id": candidate.listing_id,
                },
            )
            created = existing_event_id is None
            run_update = connection.execute(
                """
                UPDATE ingest.run
                SET events_new = COALESCE(events_new, 0) + %(new_count)s,
                    events_updated = COALESCE(events_updated, 0) + %(updated_count)s
                WHERE id = %(run_id)s
                """,
                {
                    "new_count": int(created),
                    "updated_count": int(not created),
                    "run_id": candidate.ingestion_run_id,
                },
            )
            if run_update.rowcount != 1:
                raise LookupError(f"ingest.run {candidate.ingestion_run_id} does not exist")
        return "created" if created else "updated"


def canonicalize_pending(
    *, repository: CanonicalEventRepository, clock: Clock
) -> CanonicalizationSummary:
    """Upsert every listing whose normalized address has a cached venue."""
    candidates = repository.candidates()
    created = 0
    updated = 0
    unchanged = 0
    awaiting_geocode = 0
    for candidate in candidates:
        venue_id = repository.cached_venue_id(candidate.normalized_address)
        if venue_id is None:
            awaiting_geocode += 1
            continue
        action = repository.upsert(
            candidate=candidate,
            venue_id=venue_id,
            written_at=clock(),
        )
        if action == "created":
            created += 1
        elif action == "updated":
            updated += 1
        else:
            unchanged += 1
    return CanonicalizationSummary(
        candidates=len(candidates),
        created=created,
        updated=updated,
        unchanged=unchanged,
        awaiting_geocode=awaiting_geocode,
    )
