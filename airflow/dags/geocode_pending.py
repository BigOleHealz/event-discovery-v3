"""Hourly cache-first venue geocoding DAG (Phase 2c)."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from airflow.sdk import dag, task

from ingestion.canonicalization import CanonicalEventRepository, canonicalize_pending
from ingestion.clock import utc_now
from ingestion.database import IngestionRepository
from ingestion.geocode_pipeline import geocode_pending
from ingestion.geocode_repository import GeocodeRepository
from ingestion.geocoding import GoogleGeocoder, GoogleGeocodingConfig


@dag(
    dag_id="geocode_pending",
    schedule=os.environ.get("GOOGLE_GEOCODING_DAG_SCHEDULE", "0 * * * *"),
    start_date=datetime(2026, 8, 27, tzinfo=UTC),
    catchup=False,
    tags=["ingestion", "geocoding"],
)
def build_geocode_pending():
    """Build the hourly geocode-to-canonical pipeline."""

    @task
    def resolve_pending_venues() -> dict[str, int]:
        database_url = os.environ.get("EVENT_DATABASE_URL", "").strip()
        if not database_url:
            raise ValueError("EVENT_DATABASE_URL is required")
        config = GoogleGeocodingConfig.from_env()
        with GoogleGeocoder(config) as geocoder:
            summary = geocode_pending(
                repository=GeocodeRepository(database_url),
                ingestion_repository=IngestionRepository(database_url),
                geocoder=geocoder,
                clock=utc_now,
            )
        return {
            "pending_listings": summary.pending_listings,
            "unique_addresses": summary.unique_addresses,
            "api_calls": summary.api_calls,
            "cache_hits": summary.cache_hits,
            "rejected_no_location": summary.rejected_no_location,
        }

    @task
    def upsert_canonical_events() -> dict[str, int]:
        database_url = os.environ.get("EVENT_DATABASE_URL", "").strip()
        if not database_url:
            raise ValueError("EVENT_DATABASE_URL is required")
        summary = canonicalize_pending(
            repository=CanonicalEventRepository(database_url),
            clock=utc_now,
        )
        return {
            "candidates": summary.candidates,
            "created": summary.created,
            "updated": summary.updated,
            "unchanged": summary.unchanged,
            "awaiting_geocode": summary.awaiting_geocode,
        }

    resolve_pending_venues() >> upsert_canonical_events()


geocode_pending_dag = build_geocode_pending()
