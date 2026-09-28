from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest
from neo4j import GraphDatabase
from psycopg.types.json import Jsonb
from test_graph import graph_config  # noqa: F401 -- real graph fixture

from ingestion.canonicalization import CanonicalEventRepository, canonicalize_pending
from ingestion.database import IngestionRepository
from ingestion.dedup import dedup_pending
from ingestion.embeddings import EmbeddingClient, EmbeddingConfig, embed_pending
from ingestion.geocode_pipeline import geocode_pending
from ingestion.geocode_repository import GeocodeRepository
from ingestion.geocoding import (
    GeocodedVenue,
    GeocodingNotFound,
    GoogleGeocoder,
    GoogleGeocodingConfig,
)
from ingestion.graph import GraphConfig, project_to_neo4j
from ingestion.site_extraction import PlanModel, replay
from ingestion.site_jobs import (
    close_site_run,
    collect_site_ids,
    configured_sites,
    fetch_site_pages,
    open_site_run,
    parse_site_events,
    stage_site_events,
)
from ingestion.site_models import EVENT_SCHEMA, SiteAdapter
from ingestion.site_pipeline import SiteClient, SiteProcessConfig, crawl_site
from ingestion.site_repository import SiteRepository

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures"
NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)
MARKET = "8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed"
CONFIG = SiteProcessConfig("https://browser.test", "test-token", "https://model.test", "test-key")


def html() -> str:
    return (FIXTURES / "site/page.html").read_text()


def plan() -> dict[str, object]:
    response = json.loads((FIXTURES / "site/plan-response.json").read_text())
    return json.loads(response["output"][0]["content"][0]["text"])


def adapter(method: str = "http", pagination: str = "next_link") -> SiteAdapter:
    return SiteAdapter(
        "fixture-site",
        method,
        {"schema": EVENT_SCHEMA, "instruction": "Read events"},
        {"kind": pagination, "parameter": "page", "wait_for": "[data-page='{page}']"},
        "fixture-model",
        1,
    )


def seed(database_url: str, source: SiteAdapter | None = None) -> SiteAdapter:
    source = source or adapter()
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        connection.execute("DELETE FROM ingest.source_adapter WHERE source='fixture-site'")
        connection.execute(
            """
            INSERT INTO ingest.source_adapter
                (source, fetch_method, priority, extraction, pagination, model, prompt_version,
                 enabled)
            VALUES (%s,%s,5,%s,%s,%s,%s,false)
        """,
            (
                source.source,
                source.fetch_method,
                Jsonb(source.extraction),
                Jsonb(source.pagination),
                source.model,
                source.prompt_version,
            ),
        )
        connection.execute(
            """UPDATE ingest.source_adapter SET enabled=true, access_policy=%s,
               min_request_interval_seconds=1 WHERE source=%s""",
            (
                Jsonb(
                    {
                        "status": "reviewed",
                        "reviewed_at": NOW.isoformat(),
                        "listing_urls": ["https://fixture.test/events"],
                        "references": ["https://fixture.test/policy"],
                        "notes": "Synthetic fixture only",
                    }
                ),
                source.source,
            ),
        )
        connection.execute(
            """
            INSERT INTO ingest.crawl_target
                (id, source, market_id, source_location, category, enabled, window_days, page_cap)
            VALUES (%s,%s,%s,%s,'music',true,5,4)
        """,
            (
                uuid.uuid4(),
                source.source,
                MARKET,
                Jsonb({"kind": "listing_url", "url": "https://fixture.test/events"}),
            ),
        )
    return source


class RecordedServices:
    def __init__(self) -> None:
        self.fetches = 0
        self.models = 0
        self.page = html()
        self.plan = plan()
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "model.test":
            self.models += 1
            body = json.loads(request.content)
            assert body["text"]["format"]["type"] == "json_schema"
            assert body["store"] is False
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "model": "fixture-model-snapshot",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": json.dumps(self.plan)},
                            ],
                        }
                    ],
                },
            )
        self.fetches += 1
        if request.url.host == "browser.test":
            body = json.loads(request.content)
            is_second = bool(body["steps"]) or "page=2" in body["url"]
            content = (FIXTURES / "site/empty.html").read_text() if is_second else self.page
            return httpx.Response(
                200,
                json={
                    "url": body["url"],
                    "http_status": 200,
                    "html": content,
                },
            )
        assert request.url.host == "fixture.test", "no unrecorded service is allowed"
        content = (
            (FIXTURES / "site/empty.html").read_text()
            if request.url.params.get("page") == "2"
            else self.page
        )
        return httpx.Response(200, text=content)


def crawl(
    database_url: str, source: SiteAdapter, services: RecordedServices, name: str = "run"
) -> dict[str, object]:
    context = open_site_run(
        database_url,
        configured_sites(database_url, source.fetch_method)[0],
        "test-sites",
        name,
        lambda: NOW,
    )
    targets = IngestionRepository(database_url).enabled_crawl_targets(source=source.source)
    with httpx.Client(transport=httpx.MockTransport(services)) as http:
        result = crawl_site(
            adapter=source,
            targets=targets,
            run_id=uuid.UUID(str(context["run_id"])),
            repository=SiteRepository(database_url),
            client=SiteClient(http, CONFIG),
            derive=PlanModel(http, CONFIG.extraction_api_url, CONFIG.extraction_api_key).derive,
            clock=lambda: NOW,
        )
    return {**context, "appearances": result.appearances, "partial_reason": result.partial_reason}


@pytest.mark.usefixtures("clean_ingestion_tables")
@pytest.mark.parametrize(
    ("method", "pagination"),
    [
        ("http", "next_link"),
        ("http", "query"),
        ("stagehand", "next_link"),
        ("stagehand", "action"),
    ],
)
def test_cached_extraction_and_pipeline_idempotency(
    database_url: str,
    method: str,
    pagination: str,
) -> None:
    source = seed(database_url, adapter(method, pagination))
    services = RecordedServices()
    for name in ("first", "first", "next-night"):
        context = crawl(database_url, source, services, name)
        context = collect_site_ids(database_url, context)
        assert context["events_found"] == 1
        for _ in range(2):
            stage_site_events(database_url, context, lambda: NOW)
            context = parse_site_events(database_url, context, lambda: NOW)
            close_site_run(database_url, context, lambda: NOW)
    assert services.models == 1
    assert services.fetches == 4  # two pages per night; retry uses saved page snapshots
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        assert connection.execute("SELECT count(*) FROM source_listing").fetchone() == (1,)
        assert connection.execute("""
            SELECT extraction_model, extraction_prompt_version FROM source_listing
        """).fetchone() == ("fixture-model-snapshot", 1)
        assert connection.execute("SELECT count(*) FROM ingest.page_fetch").fetchone() == (4,)
        assert connection.execute("""
            SELECT count(*) FROM ingest.run WHERE window_start IS NOT NULL OR window_end IS NOT NULL
        """).fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM ingest.extraction_plan").fetchone() == (1,)
    if pagination == "action":
        body = json.loads([r for r in services.requests if r.url.host == "browser.test"][1].content)
        assert body["steps"][0]["delay_ms"] == 1


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_validation_failure_invalidates_and_rederives_once(database_url: str) -> None:
    source = seed(database_url)
    services = RecordedServices()
    crawl(database_url, source, services, "initial")
    services.page = services.page.replace("<h2>", "<h3>").replace("</h2>", "</h3>")
    services.plan["fields"]["title"]["selector"] = "h3"
    crawl(database_url, source, services, "redesign")
    assert services.models == 2
    services.page = services.page.replace("2026-09-29T18:00:00-04:00", "not a date")
    with pytest.raises(ValueError, match="after derivation"):
        crawl(database_url, source, services, "broken")
    assert services.models == 3
    with pytest.raises(ValueError, match="already failed"):
        crawl(database_url, source, services, "broken")
    assert services.models == 3
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        assert connection.execute("SELECT count(*) FROM ingest.extraction_plan").fetchone() == (0,)
        assert connection.execute("""
            SELECT max(validation_failures) FROM ingest.site_page
        """).fetchone() == (2,)
        assert connection.execute("""
            SELECT count(*) FROM ingest.page_fetch WHERE error_message IS NOT NULL
        """).fetchone() == (1,)


@pytest.mark.parametrize(
    "field", ["model", "prompt_version", "extraction", "pagination", "fetch_method"]
)
def test_config_changes_invalidate_cache_key(field: str) -> None:
    source = adapter()
    values = {
        "model": "other",
        "prompt_version": 2,
        "extraction": {**source.extraction, "instruction": "Different instruction"},
        "pagination": {"kind": "none"},
        "fetch_method": "stagehand",
    }
    assert replace(source, **{field: values[field]}).config_hash != source.config_hash


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_schema_validation_never_resolves_remote_references(keyword: str) -> None:
    with pytest.raises(ValueError, match="references"):
        replace(
            adapter(),
            extraction={
                "instruction": "Read events",
                "schema": {keyword: "https://never-fetch.test/schema"},
            },
        )


def test_empty_is_valid_only_with_explicit_empty_marker_and_url_must_be_http() -> None:
    assert (
        replay(
            plan(),
            (FIXTURES / "site/empty.html").read_text(),
            "https://fixture.test",
            adapter(),
            "America/New_York",
        ).events
        == []
    )
    for content in (
        "<html>Redesigned</html>",
        html().replace("/events/concert-1", "javascript:bad"),
    ):
        with pytest.raises(ValueError):
            replay(plan(), content, "https://fixture.test", adapter(), "America/New_York")


def test_model_refusals_and_input_limits_do_not_produce_a_plan() -> None:
    calls = []

    def refused(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "fixture",
                "output": [{"type": "message", "content": [{"type": "refusal"}]}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(refused)) as client:
        with pytest.raises(ValueError, match="input limit"):
            PlanModel(client, CONFIG.extraction_api_url, "key", 1).derive(adapter(), html())
        assert not calls
        with pytest.raises(ValueError, match="missing a plan"):
            PlanModel(client, CONFIG.extraction_api_url, "key").derive(adapter(), html())
        assert len(calls) == 1


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_bad_runtime_configuration_closes_open_run_as_failed(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed(database_url)
    context = open_site_run(
        database_url,
        configured_sites(database_url, "http")[0],
        "test-sites",
        "invalid-runtime",
        lambda: NOW,
    )
    monkeypatch.setenv("SITE_REQUEST_TIMEOUT_SECONDS", "-1")
    with pytest.raises(ValueError, match="timeout"):
        fetch_site_pages(database_url, context, lambda: NOW)
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        assert connection.execute(
            "SELECT status FROM ingest.run WHERE id=%s", (context["run_id"],)
        ).fetchone() == ("failed",)


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_config_version_change_derives_new_plan_and_preserves_provenance(database_url: str) -> None:
    source = seed(database_url)
    services = RecordedServices()
    crawl(database_url, source, services, "before")
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        connection.execute(
            "UPDATE ingest.source_adapter SET prompt_version=2 WHERE source=%s", (source.source,)
        )
    updated = replace(source, prompt_version=2)
    context = collect_site_ids(database_url, crawl(database_url, updated, services, "after"))
    stage_site_events(database_url, context, lambda: NOW)
    assert services.models == 2
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        assert connection.execute(
            "SELECT extraction_prompt_version FROM source_listing"
        ).fetchone() == (2,)


@pytest.mark.usefixtures("clean_ingestion_tables")
@pytest.mark.parametrize("mode", ["cap", "repeated_ids", "cycle", "rate_limited"])
def test_pagination_guards_and_partial_results(database_url: str, mode: str) -> None:
    source = seed(
        database_url, adapter(pagination="query" if mode == "repeated_ids" else "next_link")
    )
    services = RecordedServices()
    if mode == "cap":
        with psycopg.connect(SiteRepository(database_url).url) as connection:
            connection.execute(
                "UPDATE ingest.crawl_target SET page_cap=1 WHERE source=%s", (source.source,)
            )
    if mode == "cycle":
        services.page = html().replace("?page=2", "/events")
    original = services.__call__

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fixture.test" and request.url.params.get("page") == "2":
            services.fetches += 1
            if mode == "rate_limited":
                return httpx.Response(429, text="slow down")
            if mode == "repeated_ids":
                return httpx.Response(200, text=html())
        return original(request)

    context = open_site_run(
        database_url, configured_sites(database_url, "http")[0], "test-sites", mode, lambda: NOW
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = crawl_site(
            adapter=source,
            targets=IngestionRepository(database_url).enabled_crawl_targets(source=source.source),
            run_id=uuid.UUID(str(context["run_id"])),
            repository=SiteRepository(database_url),
            client=SiteClient(http, CONFIG),
            derive=PlanModel(http, CONFIG.extraction_api_url, CONFIG.extraction_api_key).derive,
            clock=lambda: NOW,
        )
    assert result.pages <= 2
    assert (result.partial_reason is None) == (mode == "repeated_ids")
    context = collect_site_ids(
        database_url,
        {**context, "appearances": result.appearances, "partial_reason": result.partial_reason},
    )
    context = parse_site_events(database_url, context, lambda: NOW)
    close_site_run(database_url, context, lambda: NOW)
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        assert connection.execute("SELECT count(*) FROM source_listing").fetchone() == (1,)


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_geocode_failure_invalidates_extraction_plan(database_url: str) -> None:
    source = seed(database_url)
    context = collect_site_ids(database_url, crawl(database_url, source, RecordedServices()))
    parse_site_events(database_url, context, lambda: NOW)

    class MissingGeocoder:
        def geocode(self, address: str) -> GeocodedVenue:
            raise GeocodingNotFound("recorded missing address")

    result = geocode_pending(
        repository=GeocodeRepository(database_url),
        ingestion_repository=IngestionRepository(database_url),
        geocoder=MissingGeocoder(),
        clock=lambda: NOW,
    )
    assert result.rejected_no_location == 1
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        assert connection.execute("SELECT count(*) FROM ingest.extraction_plan").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM source_listing").fetchone() == (0,)


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_site_enters_existing_geocode_canonicalization_embedding_and_dedup(
    database_url: str,
    graph_config: GraphConfig,  # noqa: F811
) -> None:
    source = seed(database_url)
    context = collect_site_ids(database_url, crawl(database_url, source, RecordedServices()))
    context = parse_site_events(
        database_url, stage_site_events(database_url, context, lambda: NOW), lambda: NOW
    )
    close_site_run(database_url, context, lambda: NOW)
    geocoder_config = GoogleGeocodingConfig("key", "https://google.test", 5, "US", "en")
    with GoogleGeocoder(
        geocoder_config,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=(FIXTURES / "google/geocode_city_hall.json").read_bytes()
            )
        ),
    ) as geocoder:
        result = geocode_pending(
            repository=GeocodeRepository(database_url),
            ingestion_repository=IngestionRepository(database_url),
            geocoder=geocoder,
            clock=lambda: NOW,
        )
    assert result.api_calls == 1
    result = canonicalize_pending(
        repository=CanonicalEventRepository(database_url), clock=lambda: NOW
    )
    assert result.created == 1
    assert (
        canonicalize_pending(
            repository=CanonicalEventRepository(database_url), clock=lambda: NOW
        ).candidates
        == 0
    )
    embedding_config = EmbeddingConfig("openai", "fixture", "https://embed.test", "key", 10, 5)
    with EmbeddingClient(
        embedding_config,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0] + [0.0] * 1535}]}
            )
        ),
    ) as client:
        assert embed_pending(database_url, client) == 1
        assert embed_pending(database_url, client) == 0
    assert dedup_pending(database_url, clock=lambda: NOW)["distinct"] == 1
    assert sum(dedup_pending(database_url, clock=lambda: NOW).values()) == 0
    for _ in range(2):
        project_to_neo4j(
            database_url, graph_config, airflow_run_id="site-project", clock=lambda: NOW
        )
    with GraphDatabase.driver(
        graph_config.uri, auth=(graph_config.user, graph_config.password)
    ) as driver:
        with driver.session(database=graph_config.database) as session:
            rows = session.run("""
                MATCH (s:SourceListing {source:'fixture-site'})-[:LISTS]->(e:CanonicalEvent)
                RETURN e.title AS title
            """).data()
            assert rows == [{"title": "City Hall concert"}]
