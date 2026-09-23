"""Dispatch source payloads without coupling adapters to one another's shapes."""

from __future__ import annotations

from collections.abc import Mapping

from ingestion.eventbrite import parse_eventbrite_event, staging_identity
from ingestion.meetup import meetup_staging_identity, parse_meetup_event
from ingestion.models import ParsedListing


def source_identity(source: str, payload: Mapping[str, object]) -> tuple[str, str]:
    if source == "eventbrite":
        return staging_identity(payload)
    if source == "meetup":
        return meetup_staging_identity(payload)
    raise ValueError(f"unsupported ingestion source {source!r}")


def parse_source_listing(
    source: str,
    payload: Mapping[str, object],
    *,
    market_timezone: str,
) -> ParsedListing:
    if source == "eventbrite":
        return parse_eventbrite_event(payload)
    if source == "meetup":
        return parse_meetup_event(payload, market_timezone=market_timezone)
    raise ValueError(f"unsupported ingestion source {source!r}")
