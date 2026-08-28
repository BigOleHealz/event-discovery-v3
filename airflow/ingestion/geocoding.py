"""Google Geocoding API v4 client and response parser."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import cast
from urllib.parse import quote

import httpx

GEOCODING_FIELD_MASK = (
    "results.placeId,results.formattedAddress,results.location,results.addressComponents"
)


class GeocodingConfigurationError(ValueError):
    """Raised when required geocoding configuration is missing or invalid."""


class GeocodingResponseError(ValueError):
    """Raised when Google returns an invalid geocoding response."""


class GeocodingNotFound(LookupError):
    """Raised when Google finds no place for an address."""


@dataclass(frozen=True)
class GoogleGeocodingConfig:
    """Server-side Google Geocoding v4 configuration."""

    api_key: str
    api_base_url: str
    request_timeout_seconds: float
    region_code: str
    language_code: str

    @classmethod
    def from_env(cls) -> GoogleGeocodingConfig:
        api_key = os.environ.get("GOOGLE_GEOCODING_API_KEY", "").strip()
        api_base_url = os.environ.get("GOOGLE_GEOCODING_API_BASE_URL", "").strip().rstrip("/")
        if not api_key:
            raise GeocodingConfigurationError("GOOGLE_GEOCODING_API_KEY is required")
        if not api_base_url:
            raise GeocodingConfigurationError("GOOGLE_GEOCODING_API_BASE_URL is required")
        timeout = _positive_float_from_env("GOOGLE_GEOCODING_TIMEOUT_SECONDS", 10.0)
        return cls(
            api_key=api_key,
            api_base_url=api_base_url,
            request_timeout_seconds=timeout,
            region_code=os.environ.get("GOOGLE_GEOCODING_REGION_CODE", "US").strip() or "US",
            language_code=os.environ.get("GOOGLE_GEOCODING_LANGUAGE_CODE", "en").strip() or "en",
        )


@dataclass(frozen=True)
class GeocodedVenue:
    """Canonical venue fields selected from Google's first geocoding result."""

    google_place_id: str
    formatted_address: str
    latitude: float
    longitude: float
    city: str | None
    region: str | None
    country: str | None


class GoogleGeocoder:
    """Synchronous Geocoding v4 client for an Airflow worker."""

    def __init__(
        self,
        config: GoogleGeocodingConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.Client(
            headers={
                "X-Goog-Api-Key": config.api_key,
                "X-Goog-FieldMask": GEOCODING_FIELD_MASK,
            },
            timeout=config.request_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GoogleGeocoder:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def geocode(self, address: str) -> GeocodedVenue:
        """Resolve one postal address, raising when no result exists."""
        response = self._client.get(
            f"{self._config.api_base_url}/{quote(address, safe='')}",
            params={
                "languageCode": self._config.language_code,
                "regionCode": self._config.region_code,
            },
        )
        response.raise_for_status()
        return parse_geocoding_response(response.content)


def parse_geocoding_response(content: bytes) -> GeocodedVenue:
    """Parse the first result from a recorded Geocoding v4 response."""
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise GeocodingResponseError("Google Geocoding returned invalid JSON") from error
    if not isinstance(value, dict):
        raise GeocodingResponseError("Google Geocoding response must be an object")
    payload = cast(dict[str, object], value)
    results = payload.get("results")
    if not isinstance(results, list):
        raise GeocodingResponseError("Google Geocoding response is missing results")
    if not results:
        raise GeocodingNotFound("Google Geocoding returned no results")
    first = results[0]
    if not isinstance(first, dict):
        raise GeocodingResponseError("Google Geocoding result must be an object")
    result = cast(dict[str, object], first)
    location = _required_mapping(result, "location")
    components = result.get("addressComponents")
    if not isinstance(components, list):
        raise GeocodingResponseError("addressComponents must be a list")
    typed_components = tuple(
        cast(dict[str, object], component)
        for component in components
        if isinstance(component, dict)
    )
    return GeocodedVenue(
        google_place_id=_required_string(result, "placeId"),
        formatted_address=_required_string(result, "formattedAddress"),
        latitude=_required_number(location, "latitude"),
        longitude=_required_number(location, "longitude"),
        city=_component_text(typed_components, ("locality", "postal_town"), "longText"),
        region=_component_text(typed_components, ("administrative_area_level_1",), "shortText"),
        country=_component_text(typed_components, ("country",), "shortText"),
    )


def normalize_address(address: str) -> str:
    """Normalize a source address into the stable geocode-cache key."""
    normalized = unicodedata.normalize("NFKC", address).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", normalized).split())


def _component_text(
    components: tuple[dict[str, object], ...],
    desired_types: tuple[str, ...],
    text_field: str,
) -> str | None:
    for desired_type in desired_types:
        for component in components:
            types = component.get("types")
            if isinstance(types, list) and desired_type in types:
                value = component.get(text_field)
                if isinstance(value, str) and value:
                    return value
    return None


def _required_mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise GeocodingResponseError(f"{key} must be an object")
    return cast(dict[str, object], value)


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GeocodingResponseError(f"{key} must be a non-empty string")
    return value


def _required_number(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, int | float):
        raise GeocodingResponseError(f"{key} must be numeric")
    return float(value)


def _positive_float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip() or str(default)
    try:
        value = float(raw)
    except ValueError as error:
        raise GeocodingConfigurationError(f"{name} must be numeric") from error
    if value <= 0:
        raise GeocodingConfigurationError(f"{name} must be positive")
    return value
