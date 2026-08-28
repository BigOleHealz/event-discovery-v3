"""Cache-first geocoding workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ingestion.clock import Clock
from ingestion.database import IngestionRepository
from ingestion.geocode_repository import GeocodeRepository, PendingGeocode
from ingestion.geocoding import GeocodedVenue, GeocodingNotFound


class Geocoder(Protocol):
    def geocode(self, address: str) -> GeocodedVenue: ...


@dataclass(frozen=True)
class GeocodeSummary:
    """Observable counts for one cache-first geocoding pass."""

    pending_listings: int
    unique_addresses: int
    api_calls: int
    cache_hits: int
    rejected_no_location: int


def geocode_pending(
    *,
    repository: GeocodeRepository,
    ingestion_repository: IngestionRepository,
    geocoder: Geocoder,
    clock: Clock,
) -> GeocodeSummary:
    """Resolve each distinct pending address at most once and cache every result."""
    pending_listings = repository.pending()
    grouped = _group_by_address(pending_listings)
    api_calls = 0
    cache_hits = 0
    rejected_no_location = 0
    for normalized_address, listings in grouped.items():
        cached_venue_id = repository.cached_venue_id(normalized_address)
        if cached_venue_id is not None:
            repository.touch_cache(normalized_address, clock())
            cache_hits += 1
            continue
        representative = listings[0]
        api_calls += 1
        try:
            result = geocoder.geocode(representative.address)
        except GeocodingNotFound:
            for listing in listings:
                ingestion_repository.reject_staged_listing(
                    run_id=listing.ingestion_run_id,
                    payload=listing.payload,
                    reason="no_location",
                    rejected_at=clock(),
                )
                rejected_no_location += 1
            continue
        repository.store(pending=representative, result=result, stored_at=clock())

    return GeocodeSummary(
        pending_listings=len(pending_listings),
        unique_addresses=len(grouped),
        api_calls=api_calls,
        cache_hits=cache_hits,
        rejected_no_location=rejected_no_location,
    )


def _group_by_address(
    pending: tuple[PendingGeocode, ...],
) -> dict[str, list[PendingGeocode]]:
    grouped: dict[str, list[PendingGeocode]] = {}
    for listing in pending:
        grouped.setdefault(listing.normalized_address, []).append(listing)
    return grouped
