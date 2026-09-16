"""Embed pending source listings in Postgres; no similarity matching or merging."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import sleep
from typing import cast

import httpx
import psycopg

from ingestion.sources import parse_source_listing


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    api_base_url: str
    api_key: str
    batch_size: int
    request_timeout_seconds: float

    def __post_init__(self) -> None:
        if self.provider != "openai":
            raise ValueError("EMBEDDING_PROVIDER must be openai")
        if not self.model or not self.api_base_url or not self.api_key:
            raise ValueError(
                "EMBEDDING_MODEL, EMBEDDING_API_BASE_URL and EMBEDDING_API_KEY are required"
            )
        if self.batch_size <= 0:
            raise ValueError("EMBEDDING_BATCH_SIZE must be positive")
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("EMBEDDING_REQUEST_TIMEOUT_SECONDS must be finite and positive")

    @classmethod
    def from_env(cls) -> EmbeddingConfig:
        return cls(
            provider=os.environ.get("EMBEDDING_PROVIDER", "").strip(),
            model=os.environ.get("EMBEDDING_MODEL", "").strip(),
            api_base_url=os.environ.get("EMBEDDING_API_BASE_URL", "").strip().rstrip("/"),
            api_key=os.environ.get("EMBEDDING_API_KEY", "").strip(),
            batch_size=int(os.environ.get("EMBEDDING_BATCH_SIZE", "100")),
            request_timeout_seconds=float(
                os.environ.get("EMBEDDING_REQUEST_TIMEOUT_SECONDS", "30")
            ),
        )


def embedding_text(title: str, description: str | None) -> str:
    return title + "\n" + (description or "")[:500]


class EmbeddingClient:
    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.config = config
        self._sleeper = sleeper
        self._client = httpx.Client(
            base_url=config.api_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {config.api_key}"},
            timeout=config.request_timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> EmbeddingClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._client.close()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        for attempt in range(3):
            try:
                response = self._client.post(
                    "embeddings",
                    json={
                        "model": self.config.model,
                        "input": list(texts),
                        "encoding_format": "float",
                    },
                )
                response.raise_for_status()
                break
            except (httpx.ConnectError, httpx.TimeoutException):
                if attempt == 2:
                    raise
                self._sleeper(2**attempt)
        body = response.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("Embedding response must contain one vector per input")
        vectors: dict[int, list[float]] = {}
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("Invalid embedding response item")
            index, vector = item.get("index"), item.get("embedding")
            if type(index) is not int or index not in range(len(texts)) or index in vectors:
                raise ValueError("Invalid or duplicate embedding response index")
            if (
                not isinstance(vector, list)
                or not vector
                or any(
                    type(value) not in (int, float) or not math.isfinite(value) for value in vector
                )
            ):
                raise ValueError("Embedding must contain finite numbers")
            # Postgres owns and enforces the dimension via vector(1536).
            vectors[index] = [float(value) for value in vector]
        return [vectors[index] for index in range(len(texts))]


def embed_pending(database_url: str, client: EmbeddingClient) -> int:
    """Fill null embeddings after geocoding, committing one atomic batch at a time.

    Row locks serialize overlapping runs; failures leave the current batch retryable.
    Source-specific text comes from each listing's payload, never a merged event title.
    """
    database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    written = 0
    while True:
        with psycopg.connect(database_url) as connection:
            rows = connection.execute(
                """
                SELECT sl.id, sl.source, sl.raw_payload, market.timezone
                FROM source_listing AS sl
                JOIN canonical_event AS event ON event.id = sl.canonical_event_id
                JOIN ingest.run AS run ON run.id = sl.ingestion_run_id
                JOIN ingest.market AS market ON market.id = run.market_id
                WHERE sl.embedding IS NULL AND sl.source IN ('eventbrite', 'meetup')
                ORDER BY sl.id
                LIMIT %s
                FOR UPDATE OF sl SKIP LOCKED
                """,
                (client.config.batch_size,),
            ).fetchall()
            if not rows:
                return written
            texts = []
            for _, source, payload, timezone in rows:
                listing = parse_source_listing(
                    source, cast(dict[str, object], payload), market_timezone=timezone
                )
                texts.append(embedding_text(listing.title, listing.description))
            vectors = client.embed(texts)
            for row, vector in zip(rows, vectors, strict=True):
                connection.execute(
                    "UPDATE source_listing SET embedding = %s::vector WHERE id = %s",
                    (json.dumps(vector, allow_nan=False), row[0]),
                )
        written += len(rows)


def main() -> None:
    database_url = os.environ.get("EVENT_DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("EVENT_DATABASE_URL is required")
    with EmbeddingClient(EmbeddingConfig.from_env()) as client:
        print(f"Embedded {embed_pending(database_url, client)} source listings")


if __name__ == "__main__":
    main()
