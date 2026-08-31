"""Create and validate the single Qdrant collection used for event deduplication."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import sleep
from typing import cast

import httpx

COLLECTION_NAME = "event_listings"
PAYLOAD_INDEXES = {
    "location": "geo",
    "starts_at_epoch": "integer",
}


class QdrantConfigurationError(ValueError):
    """Raised when Qdrant process configuration is missing or invalid."""


class QdrantSchemaError(RuntimeError):
    """Raised when the existing collection is incompatible with this application."""


@dataclass(frozen=True)
class QdrantConfig:
    url: str
    api_key: str | None
    vector_size: int
    request_timeout_seconds: float
    init_attempts: int
    init_retry_seconds: float

    @classmethod
    def from_env(cls) -> QdrantConfig:
        url = os.environ.get("QDRANT_URL", "").strip().rstrip("/")
        if not url:
            raise QdrantConfigurationError("QDRANT_URL is required")
        api_key = os.environ.get("QDRANT_API_KEY", "").strip() or None
        return cls(
            url=url,
            api_key=api_key,
            vector_size=_positive_int_from_env("QDRANT_VECTOR_SIZE", 1536),
            request_timeout_seconds=_positive_float_from_env(
                "QDRANT_REQUEST_TIMEOUT_SECONDS", 10.0
            ),
            init_attempts=_positive_int_from_env("QDRANT_INIT_ATTEMPTS", 30),
            init_retry_seconds=_positive_float_from_env("QDRANT_INIT_RETRY_SECONDS", 1.0),
        )


class QdrantCollectionManager:
    """Idempotently provision and validate the fixed event-listing schema."""

    def __init__(
        self,
        config: QdrantConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"api-key": config.api_key} if config.api_key is not None else None
        self._config = config
        self._client = httpx.Client(
            base_url=config.url,
            headers=headers,
            timeout=config.request_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> QdrantCollectionManager:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def ensure_collection(self) -> None:
        response = self._client.get(f"/collections/{COLLECTION_NAME}")
        if response.status_code == 404:
            self._create_collection()
        else:
            self._raise_for_status(response)

        collection = self._collection_result()
        self._validate_vectors(collection)
        payload_schema = self._payload_schema(collection)
        for field_name, field_schema in PAYLOAD_INDEXES.items():
            existing = payload_schema.get(field_name)
            if existing is None:
                self._create_payload_index(field_name, field_schema)
                continue
            self._validate_payload_index(field_name, existing, field_schema)

        refreshed = self._collection_result()
        self._validate_vectors(refreshed)
        refreshed_payload_schema = self._payload_schema(refreshed)
        for field_name, field_schema in PAYLOAD_INDEXES.items():
            existing = refreshed_payload_schema.get(field_name)
            if existing is None:
                raise QdrantSchemaError(f"Qdrant payload index {field_name!r} was not created")
            self._validate_payload_index(field_name, existing, field_schema)

    def _create_collection(self) -> None:
        response = self._client.put(
            f"/collections/{COLLECTION_NAME}",
            params={"timeout": 30},
            json={
                "vectors": {
                    "size": self._config.vector_size,
                    "distance": "Cosine",
                }
            },
        )
        self._raise_for_status(response)

    def _create_payload_index(self, field_name: str, field_schema: str) -> None:
        response = self._client.put(
            f"/collections/{COLLECTION_NAME}/index",
            params={"wait": "true"},
            json={"field_name": field_name, "field_schema": field_schema},
        )
        self._raise_for_status(response)

    def _collection_result(self) -> Mapping[str, object]:
        response = self._client.get(f"/collections/{COLLECTION_NAME}")
        self._raise_for_status(response)
        body = self._json_object(response)
        result = body.get("result")
        if not isinstance(result, dict):
            raise QdrantSchemaError("Qdrant collection response has no result object")
        return cast(dict[str, object], result)

    def _validate_vectors(self, collection: Mapping[str, object]) -> None:
        config = collection.get("config")
        if not isinstance(config, dict) or not isinstance(config.get("params"), dict):
            raise QdrantSchemaError("Qdrant collection response has no config.params object")
        vectors = config["params"].get("vectors")
        if not isinstance(vectors, dict):
            raise QdrantSchemaError("event_listings must have one unnamed dense vector")
        size = vectors.get("size")
        distance = vectors.get("distance")
        if size != self._config.vector_size:
            raise QdrantSchemaError(
                f"event_listings vector size is {size!r}; expected {self._config.vector_size}"
            )
        if distance != "Cosine":
            raise QdrantSchemaError(
                f"event_listings distance is {distance!r}; expected 'Cosine'"
            )

    @staticmethod
    def _payload_schema(collection: Mapping[str, object]) -> Mapping[str, object]:
        payload_schema = collection.get("payload_schema")
        if not isinstance(payload_schema, dict):
            raise QdrantSchemaError("Qdrant collection response has no payload_schema object")
        return cast(dict[str, object], payload_schema)

    @staticmethod
    def _validate_payload_index(field_name: str, existing: object, expected: str) -> None:
        if not isinstance(existing, dict):
            raise QdrantSchemaError(f"Qdrant payload index {field_name!r} is malformed")
        data_type = existing.get("data_type")
        if data_type != expected:
            raise QdrantSchemaError(
                f"Qdrant payload index {field_name!r} is {data_type!r}; expected {expected!r}"
            )

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, object]:
        try:
            body = response.json()
        except ValueError as error:
            raise QdrantSchemaError("Qdrant returned invalid JSON") from error
        if not isinstance(body, dict):
            raise QdrantSchemaError("Qdrant response must be a JSON object")
        return cast(dict[str, object], body)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = response.text[:1000]
            raise QdrantSchemaError(
                f"Qdrant {response.request.method} {response.request.url.path} failed "
                f"with {response.status_code}: {detail}"
            ) from error


def initialize_qdrant(
    config: QdrantConfig,
    *,
    transport: httpx.BaseTransport | None = None,
    sleeper: Callable[[float], None] = sleep,
) -> None:
    """Retry startup connectivity, but never retry an incompatible schema."""
    for attempt in range(1, config.init_attempts + 1):
        try:
            with QdrantCollectionManager(config, transport=transport) as manager:
                manager.ensure_collection()
            return
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            if attempt == config.init_attempts:
                raise
            sleeper(config.init_retry_seconds)


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError as error:
        raise QdrantConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise QdrantConfigurationError(f"{name} must be positive")
    return value


def _positive_float_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError as error:
        raise QdrantConfigurationError(f"{name} must be numeric") from error
    if value <= 0:
        raise QdrantConfigurationError(f"{name} must be positive")
    return value


def main() -> None:
    initialize_qdrant(QdrantConfig.from_env())
    print(f"Qdrant collection {COLLECTION_NAME!r} is ready")


if __name__ == "__main__":
    main()
