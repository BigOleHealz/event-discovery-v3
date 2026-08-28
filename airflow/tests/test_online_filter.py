from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from ingestion.eventbrite import parse_eventbrite_event
from ingestion.models import ParsedEventbriteListing
from ingestion.online_filter import apply_online_filter

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "eventbrite"
    / "organization_events_page_1.json"
)


def real_listing() -> ParsedEventbriteListing:
    page = json.loads(FIXTURE_PATH.read_text())
    if not isinstance(page, dict):
        raise TypeError("recorded Eventbrite page must be an object")
    events = page.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[0], dict):
        raise TypeError("recorded Eventbrite page must contain an event")
    return parse_eventbrite_event(cast(dict[str, object], events[0]))


def test_layer_one_rejects_eventbrite_online_flag() -> None:
    decision = apply_online_filter(replace(real_listing(), online_event=True))

    assert decision.keep is False
    assert decision.reason == "online"
    assert decision.rule == "source_flag"


def test_layer_two_rejects_virtual_venue_heuristic() -> None:
    decision = apply_online_filter(replace(real_listing(), venue_name="Zoom Webinar"))

    assert decision.keep is False
    assert decision.reason == "online"
    assert decision.rule == "venue_heuristic"


def test_layer_three_uses_content_only_without_a_physical_location() -> None:
    listing = replace(
        real_listing(),
        title="Virtual community meetup",
        venue_name=None,
        venue_address=None,
        latitude=None,
        longitude=None,
    )
    decision = apply_online_filter(listing)

    assert decision.keep is False
    assert decision.reason == "online"
    assert decision.rule == "content_keyword"


def test_layer_four_rejects_listing_with_no_physical_location() -> None:
    listing = replace(
        real_listing(),
        venue_name=None,
        venue_address="To be announced",
        latitude=None,
        longitude=None,
    )
    decision = apply_online_filter(listing)

    assert decision.keep is False
    assert decision.reason == "no_location"
    assert decision.rule == "no_location"


def test_hybrid_event_with_real_venue_and_stream_is_kept() -> None:
    listing = replace(
        real_listing(),
        description="Attend at City Hall or watch the livestream from home.",
    )
    decision = apply_online_filter(listing)

    assert decision.keep is True
    assert decision.reason is None
    assert decision.rule is None
