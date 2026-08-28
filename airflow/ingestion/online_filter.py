"""Layered in-person filtering for parsed source listings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ingestion.models import ParsedEventbriteListing

RejectionReason = Literal["online", "no_location"]
FilterRule = Literal["source_flag", "venue_heuristic", "content_keyword", "no_location"]

ONLINE_TERMS = re.compile(
    r"\b(?:online|virtual|webinar|zoom|google\s+meet|teams|"
    r"livestream(?:ed|ing)?|live\s+stream(?:ed|ing)?|remote)\b",
    re.IGNORECASE,
)
PLACEHOLDER_ADDRESSES = frozenset(
    {
        "",
        "n/a",
        "na",
        "none",
        "not applicable",
        "see description",
        "tba",
        "to be announced",
        "to be determined",
    }
)


@dataclass(frozen=True)
class OnlineFilterDecision:
    """Whether a listing survives, plus the exact rule that rejected it."""

    keep: bool
    reason: RejectionReason | None = None
    rule: FilterRule | None = None


def apply_online_filter(listing: ParsedEventbriteListing) -> OnlineFilterDecision:
    """Apply the four specification checks in cheapest-first order."""
    if listing.online_event:
        return OnlineFilterDecision(keep=False, reason="online", rule="source_flag")

    venue_text = " ".join(
        value for value in (listing.venue_name, listing.venue_address) if value is not None
    )
    if ONLINE_TERMS.search(venue_text):
        return OnlineFilterDecision(keep=False, reason="online", rule="venue_heuristic")

    has_physical_location = _has_physical_location(listing)
    if not has_physical_location:
        content_text = " ".join(
            value for value in (listing.title, listing.description) if value is not None
        )
        if ONLINE_TERMS.search(content_text):
            return OnlineFilterDecision(keep=False, reason="online", rule="content_keyword")
        return OnlineFilterDecision(keep=False, reason="no_location", rule="no_location")

    return OnlineFilterDecision(keep=True)


def _has_physical_location(listing: ParsedEventbriteListing) -> bool:
    if listing.latitude is not None and listing.longitude is not None:
        return True
    if listing.venue_address is None:
        return False
    normalized_address = " ".join(listing.venue_address.lower().split())
    return normalized_address not in PLACEHOLDER_ADDRESSES
