"""Database cache and venue writes for geocoding."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import psycopg

from ingestion.geocoding import GeocodedVenue, normalize_address
from ingestion.sources import parse_source_listing

VENUE_NAMESPACE = uuid.UUID("02ee8c18-cafb-45f9-87d6-122f5d74c978")


@dataclass(frozen=True)
class PendingGeocode:
    """One staged listing that still needs an address-to-venue association."""

    ingestion_run_id: uuid.UUID
    source: str
    source_event_id: str
    payload: dict[str, object]
    address: str
    normalized_address: str
    venue_name: str | None


class GeocodeRepository:
    """Read pending addresses and persist cache-backed canonical venues."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def pending(self) -> tuple[PendingGeocode, ...]:
        """Return accepted, not-yet-canonicalized source listings with addresses."""
        with psycopg.connect(self._database_url) as connection:
            rows = connection.execute(
                """
                SELECT sl.ingestion_run_id, sl.source, sl.source_event_id, sl.raw_payload,
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
        pending: list[PendingGeocode] = []
        for ingestion_run_id, source, source_event_id, payload, market_timezone in rows:
            if not isinstance(payload, dict):
                raise TypeError("source_listing.raw_payload must be a JSON object")
            if not isinstance(source, str) or not isinstance(market_timezone, str):
                raise TypeError("source and market timezone must be text")
            listing = parse_source_listing(source, payload, market_timezone=market_timezone)
            if listing.venue_address is None:
                continue
            pending.append(
                PendingGeocode(
                    ingestion_run_id=ingestion_run_id,
                    source=source,
                    source_event_id=source_event_id,
                    payload=payload,
                    address=listing.venue_address,
                    normalized_address=normalize_address(listing.venue_address),
                    venue_name=listing.venue_name,
                )
            )
        return tuple(pending)

    def cached_venue_id(self, normalized_address: str) -> uuid.UUID | None:
        """Resolve a normalized source address without spending an API call."""
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                """
                SELECT venue_id
                FROM ingest.geocode_cache
                WHERE normalized_address = %(normalized_address)s
                """,
                {"normalized_address": normalized_address},
            ).fetchone()
        return row[0] if row is not None else None

    def touch_cache(self, normalized_address: str, used_at: datetime) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                UPDATE ingest.geocode_cache
                SET last_used_at = %(used_at)s
                WHERE normalized_address = %(normalized_address)s
                """,
                {"normalized_address": normalized_address, "used_at": used_at},
            )

    def store(
        self,
        *,
        pending: PendingGeocode,
        result: GeocodedVenue,
        stored_at: datetime,
    ) -> uuid.UUID:
        """Upsert the place-id venue and its normalized source-address alias atomically."""
        proposed_venue_id = uuid.uuid5(VENUE_NAMESPACE, result.google_place_id)
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute(
                """
                INSERT INTO venue (
                    id, name, formatted_address, google_place_id, location,
                    city, region, country
                ) VALUES (
                    %(id)s, %(name)s, %(formatted_address)s, %(google_place_id)s,
                    ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326)::geography,
                    %(city)s, %(region)s, %(country)s
                )
                ON CONFLICT (google_place_id) DO UPDATE SET
                    name = COALESCE(venue.name, EXCLUDED.name),
                    formatted_address = EXCLUDED.formatted_address,
                    location = EXCLUDED.location,
                    city = EXCLUDED.city,
                    region = EXCLUDED.region,
                    country = EXCLUDED.country
                RETURNING id
                """,
                {
                    "id": proposed_venue_id,
                    "name": pending.venue_name,
                    "formatted_address": result.formatted_address,
                    "google_place_id": result.google_place_id,
                    "longitude": result.longitude,
                    "latitude": result.latitude,
                    "city": result.city,
                    "region": result.region,
                    "country": result.country,
                },
            ).fetchone()
            if row is None:
                raise RuntimeError("venue upsert returned no id")
            venue_id = row[0]
            if not isinstance(venue_id, uuid.UUID):
                raise TypeError("venue upsert returned a non-UUID id")
            connection.execute(
                """
                INSERT INTO ingest.geocode_cache (
                    normalized_address, input_address, venue_id, google_place_id,
                    created_at, last_used_at
                ) VALUES (
                    %(normalized_address)s, %(input_address)s, %(venue_id)s,
                    %(google_place_id)s, %(stored_at)s, %(stored_at)s
                )
                ON CONFLICT (normalized_address) DO UPDATE SET
                    input_address = EXCLUDED.input_address,
                    venue_id = EXCLUDED.venue_id,
                    google_place_id = EXCLUDED.google_place_id,
                    last_used_at = EXCLUDED.last_used_at
                """,
                {
                    "normalized_address": pending.normalized_address,
                    "input_address": pending.address,
                    "venue_id": venue_id,
                    "google_place_id": result.google_place_id,
                    "stored_at": stored_at,
                },
            )
        return venue_id
