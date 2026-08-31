from __future__ import annotations

import json

import httpx
import pytest

from ingestion.qdrant import (
    COLLECTION_NAME,
    QdrantCollectionManager,
    QdrantConfig,
    QdrantConfigurationError,
    QdrantSchemaError,
    initialize_qdrant,
)


def config(*, vector_size: int = 1536, init_attempts: int = 3) -> QdrantConfig:
    return QdrantConfig(
        url="http://qdrant.test:6333",
        api_key="recorded-qdrant-key",
        vector_size=vector_size,
        request_timeout_seconds=5,
        init_attempts=init_attempts,
        init_retry_seconds=0.25,
    )


class FakeQdrant:
    def __init__(self, *, existing: bool = False, vector_size: int = 1536) -> None:
        self.exists = existing
        self.vector_size = vector_size
        self.payload_schema: dict[str, dict[str, str]] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["api-key"] == "recorded-qdrant-key"
        collection_path = f"/collections/{COLLECTION_NAME}"
        if request.method == "GET" and request.url.path == collection_path:
            if not self.exists:
                return httpx.Response(404, json={"status": {"error": "not found"}}, request=request)
            return httpx.Response(200, json=self.collection_response(), request=request)
        if request.method == "PUT" and request.url.path == collection_path:
            body = self.request_body(request)
            vectors = body.get("vectors")
            assert vectors == {"size": 1536, "distance": "Cosine"}
            self.exists = True
            self.vector_size = 1536
            return httpx.Response(200, json={"result": True, "status": "ok"}, request=request)
        if request.method == "PUT" and request.url.path == f"{collection_path}/index":
            body = self.request_body(request)
            field_name = body.get("field_name")
            field_schema = body.get("field_schema")
            assert isinstance(field_name, str) and isinstance(field_schema, str)
            self.payload_schema[field_name] = {"data_type": field_schema}
            return httpx.Response(
                200,
                json={"result": {"operation_id": 1, "status": "completed"}, "status": "ok"},
                request=request,
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    def collection_response(self) -> dict[str, object]:
        return {
            "result": {
                "status": "green",
                "config": {
                    "params": {
                        "vectors": {
                            "size": self.vector_size,
                            "distance": "Cosine",
                        }
                    }
                },
                "payload_schema": self.payload_schema,
            },
            "status": "ok",
        }

    @staticmethod
    def request_body(request: httpx.Request) -> dict[str, object]:
        body = json.loads(request.content)
        if not isinstance(body, dict):
            raise TypeError("Qdrant request body must be an object")
        return body


def test_initializer_creates_one_cosine_collection_and_required_indexes() -> None:
    fake = FakeQdrant()

    with QdrantCollectionManager(
        config(), transport=httpx.MockTransport(fake.handler)
    ) as manager:
        manager.ensure_collection()

    assert fake.exists
    assert fake.payload_schema == {
        "location": {"data_type": "geo"},
        "starts_at_epoch": {"data_type": "integer"},
    }
    writes = [request for request in fake.requests if request.method == "PUT"]
    assert [request.url.path for request in writes] == [
        f"/collections/{COLLECTION_NAME}",
        f"/collections/{COLLECTION_NAME}/index",
        f"/collections/{COLLECTION_NAME}/index",
    ]


def test_initializer_is_idempotent_when_collection_schema_matches() -> None:
    fake = FakeQdrant(existing=True)
    fake.payload_schema = {
        "location": {"data_type": "geo"},
        "starts_at_epoch": {"data_type": "integer"},
    }

    with QdrantCollectionManager(
        config(), transport=httpx.MockTransport(fake.handler)
    ) as manager:
        manager.ensure_collection()
        manager.ensure_collection()

    assert all(request.method == "GET" for request in fake.requests)


def test_initializer_rejects_incompatible_existing_vector_size() -> None:
    fake = FakeQdrant(existing=True, vector_size=768)

    with QdrantCollectionManager(
        config(), transport=httpx.MockTransport(fake.handler)
    ) as manager:
        with pytest.raises(QdrantSchemaError, match="vector size is 768"):
            manager.ensure_collection()


def test_startup_retries_connectivity_without_retrying_schema_errors() -> None:
    fake = FakeQdrant()
    connection_attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal connection_attempts
        connection_attempts += 1
        if connection_attempts == 1:
            raise httpx.ConnectError("not ready", request=request)
        return fake.handler(request)

    initialize_qdrant(
        config(),
        transport=httpx.MockTransport(handler),
        sleeper=sleeps.append,
    )

    assert sleeps == [0.25]
    assert fake.exists


def test_config_rejects_nonpositive_vector_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("QDRANT_VECTOR_SIZE", "0")

    with pytest.raises(QdrantConfigurationError, match="QDRANT_VECTOR_SIZE must be positive"):
        QdrantConfig.from_env()
