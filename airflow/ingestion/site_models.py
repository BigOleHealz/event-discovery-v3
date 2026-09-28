"""Validated site inventory and the shared, versioned extracted-listing contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator

from ingestion.models import ParsedListing

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
REQUIRED = ("source_event_id", "url", "title", "starts_at", "venue_address")
EVENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {field: {"type": ["string", "null"]} for field in FIELDS},
    "required": list(REQUIRED),
}


def http_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("expected an HTTP(S) URL without credentials")
    return value


@dataclass(frozen=True)
class SiteAdapter:
    source: str
    fetch_method: str
    extraction: dict[str, object]
    pagination: dict[str, object]
    model: str
    prompt_version: int

    def __post_init__(self) -> None:
        if self.fetch_method not in ("http", "stagehand") or not self.model.strip():
            raise ValueError("invalid site adapter method or model")
        if self.prompt_version <= 0:
            raise ValueError("prompt_version must be positive")
        if (
            not isinstance(self.extraction.get("instruction"), str)
            or not str(self.extraction["instruction"]).strip()
        ):
            raise ValueError("extraction.instruction is required")
        schema = self.extraction.get("schema")
        if not isinstance(schema, dict):
            raise ValueError("extraction.schema must be a JSON schema object")
        # Remote schema references would turn validation into an external fetch.
        if any(f'"{key}"' in json.dumps(schema) for key in ("$ref", "$dynamicRef")):
            raise ValueError("schema references are not supported")
        Draft202012Validator.check_schema(schema)
        venue = self.extraction.get("venue", {})
        if not isinstance(venue, dict) or any(
            key not in FIELDS
            or not key.startswith("venue_")
            or not isinstance(value, str)
            or not value.strip()
            for key, value in venue.items()
        ):
            raise ValueError("venue defaults must contain nonempty venue fields only")
        filters = self.extraction.get("card_filter", {})
        if not isinstance(filters, dict) or any(
            key not in ("include_all", "exclude_any")
            or not isinstance(value, list)
            or any(not isinstance(word, str) or not word.strip() for word in value)
            for key, value in filters.items()
        ):
            raise ValueError("invalid card_filter")
        if self.extraction.get("identity") not in (None, "title_start_venue"):
            raise ValueError("unsupported site identity strategy")
        if self.pagination.get("kind") not in ("none", "query", "next_link", "action"):
            raise ValueError("unsupported pagination kind")
        if self.pagination["kind"] == "query":
            if not isinstance(self.pagination.get("parameter"), str):
                raise ValueError("query pagination requires a parameter")
        if self.pagination["kind"] == "action":
            if self.fetch_method != "stagehand" or not self.pagination.get("wait_for"):
                raise ValueError("action pagination requires Stagehand and wait_for template")

    @property
    def config_hash(self) -> str:
        value = {
            "engine": "site-plan-v2",
            "source": self.source,
            "method": self.fetch_method,
            "extraction": self.extraction,
            "pagination": self.pagination,
            "model": self.model,
            "prompt_version": self.prompt_version,
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def site_event(payload: Mapping[str, object], *, market_timezone: str) -> ParsedListing:
    if payload.get("_format") != "site-v1" or not isinstance(payload.get("event"), dict):
        raise ValueError("unsupported site payload format")
    event = cast(dict[str, object], payload["event"])
    Draft202012Validator(EVENT_SCHEMA).validate(event)
    for field in REQUIRED:
        if not isinstance(event.get(field), str) or not str(event[field]).strip():
            raise ValueError(f"missing required site field: {field}")
    timezone = ZoneInfo(market_timezone)

    def instant(field: str) -> datetime:
        parsed = datetime.fromisoformat(str(event[field]))
        if "T" not in str(event[field]):
            raise ValueError(f"{field} must include a time")
        return parsed.replace(tzinfo=timezone) if parsed.tzinfo is None else parsed

    start = instant("starts_at")
    end = instant("ends_at") if event.get("ends_at") else None
    if end is not None and end < start:
        raise ValueError("end precedes start")

    def optional(field: str) -> str | None:
        value = event.get(field)
        return str(value).strip() if value else None

    return ParsedListing(
        source_event_id=str(event["source_event_id"]).strip(),
        url=http_url(str(event["url"])),
        title=str(event["title"]).strip(),
        description=optional("description"),
        starts_at=start,
        ends_at=end,
        timezone=market_timezone,
        online_event=False,
        venue_name=optional("venue_name"),
        venue_address=optional("venue_address"),
        venue_city=optional("venue_city"),
        venue_region=optional("venue_region"),
        venue_country=optional("venue_country"),
        latitude=None,
        longitude=None,
        primary_category=str(payload["category"]) if payload.get("category") else None,
    )
