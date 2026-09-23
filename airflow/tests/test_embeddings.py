from __future__ import annotations

import json
from dataclasses import replace

import httpx
import psycopg
import pytest
from test_canonicalization import FIRST_TIME, event_variant, psycopg_url, stage_run

from ingestion.embeddings import EmbeddingClient, EmbeddingConfig, embed_pending, embedding_text


def config() -> EmbeddingConfig:
    return EmbeddingConfig(
        "openai", "text-embedding-3-small", "https://embedding.test/v1", "key", 2, 5
    )


def test_text_uses_title_and_first_500_description_characters() -> None:
    assert embedding_text("Title", "a" * 501) == "Title\n" + "a" * 500
    assert embedding_text("Title", None) == "Title\n"


def test_client_auth_model_and_response_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://embedding.test/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer key"
        assert json.loads(request.content) == {
            "model": "text-embedding-3-small",
            "input": ["one", "two"],
            "encoding_format": "float",
        }
        return httpx.Response(
            200,
            json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]},
        )

    with EmbeddingClient(config(), transport=httpx.MockTransport(handler)) as client:
        assert client.embed(["one", "two"]) == [[1, 0], [0, 1]]
        assert client.embed([]) == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": []},
        {"data": [{"index": 1, "embedding": [1]}]},
        {"data": [{"index": 0, "embedding": []}]},
        {"data": [{"index": 0, "embedding": [True]}]},
        {"data": [{"index": 0, "embedding": ["1"]}]},
    ],
)
def test_malformed_responses_are_not_retried(body: object) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=body)

    with EmbeddingClient(config(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            client.embed(["title"])
    assert len(calls) == 1


def test_connectivity_retries_are_bounded() -> None:
    calls = []
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("unavailable", request=request)

    with EmbeddingClient(
        config(), transport=httpx.MockTransport(handler), sleeper=sleeps.append
    ) as client:
        with pytest.raises(httpx.ConnectError):
            client.embed(["title"])
    assert len(calls) == 3
    assert sleeps == [1, 2]


def test_connectivity_recovers() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("not ready", request=request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1]}]})

    with EmbeddingClient(
        config(), transport=httpx.MockTransport(handler), sleeper=lambda _: None
    ) as client:
        assert client.embed(["title"]) == [[1]]
    assert len(calls) == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "other"),
        ("model", ""),
        ("api_key", ""),
        ("api_base_url", ""),
        ("batch_size", 0),
        ("request_timeout_seconds", 0),
        ("request_timeout_seconds", float("nan")),
    ],
)
def test_invalid_configuration(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(config(), **{field: value})


def test_env_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "PROVIDER": "openai",
        "MODEL": "text-embedding-3-small",
        "API_BASE_URL": "https://embedding.test/v1/",
        "API_KEY": "key",
        "BATCH_SIZE": "2",
        "REQUEST_TIMEOUT_SECONDS": "5",
    }.items():
        monkeypatch.setenv(f"EMBEDDING_{key}", value)
    assert EmbeddingConfig.from_env() == config()


@pytest.fixture
def pending_listings(database_url: str, clean_ingestion_tables: None) -> str:
    stage_run(
        database_url,
        events=tuple(event_variant(str(i), f"Source title {i}") for i in range(4)),
        observed_at=FIRST_TIME,
    )
    with psycopg.connect(psycopg_url(database_url)) as connection:
        connection.execute("""
            INSERT INTO canonical_event (id, title, starts_at, timezone, location)
            SELECT id, 'Different canonical title', now(), 'America/New_York',
                ST_SetSRID(ST_MakePoint(-75, 40), 4326)::geography
            FROM source_listing WHERE source_event_id <> '3';
            UPDATE source_listing SET canonical_event_id = id WHERE source_event_id <> '3';
        """)
    return database_url


def test_postgres_batches_persist_and_reruns_do_not_duplicate(pending_listings: str) -> None:
    inputs = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        inputs.extend(texts)
        return httpx.Response(
            200,
            json={
                "data": [{"index": i, "embedding": [1.0] + [0.0] * 1535} for i in range(len(texts))]
            },
        )

    with EmbeddingClient(config(), transport=httpx.MockTransport(handler)) as client:
        assert embed_pending(pending_listings, client) == 3
        assert embed_pending(pending_listings, client) == 0
    assert len(inputs) == 3
    assert all(text.startswith("Source title") for text in inputs)
    with psycopg.connect(psycopg_url(pending_listings)) as connection:
        assert connection.execute(
            "SELECT count(*), count(embedding), min(vector_dims(embedding)) FROM source_listing"
        ).fetchone() == (4, 3, 1536)
        assert (
            connection.execute(
                "SELECT 1 - (embedding <=> embedding) FROM source_listing "
                "WHERE embedding IS NOT NULL"
            ).fetchall()
            == [(1.0,)] * 3
        )


def test_wrong_dimension_rolls_back_whole_batch(pending_listings: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [1.0] * 1536},
                    {"index": 1, "embedding": [1.0] * 768},
                ]
            },
        )

    with EmbeddingClient(config(), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(psycopg.errors.DataException, match="expected 1536 dimensions"):
            embed_pending(pending_listings, client)
    with psycopg.connect(psycopg_url(pending_listings)) as connection:
        assert connection.execute("SELECT count(embedding) FROM source_listing").fetchone() == (0,)
