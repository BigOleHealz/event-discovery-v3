from __future__ import annotations

import json
import runpy
import uuid
from collections import Counter
from datetime import timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from neo4j import GraphDatabase
from psycopg.types.json import Jsonb
from test_graph import graph_config  # noqa: F401
from test_sites import NOW

from ingestion.canonicalization import CanonicalEventRepository, canonicalize_pending
from ingestion.database import IngestionRepository
from ingestion.dedup import dedup_pending
from ingestion.embeddings import EmbeddingClient, EmbeddingConfig, embed_pending
from ingestion.geocode_pipeline import geocode_pending
from ingestion.geocode_repository import GeocodeRepository
from ingestion.geocoding import GeocodedVenue
from ingestion.graph import GraphConfig, project_to_neo4j
from ingestion.site_extraction import PlanModel, replay
from ingestion.site_jobs import (
    adapter_from,
    close_site_run,
    collect_site_ids,
    configured_sites,
    open_site_run,
    parse_site_events,
    stage_site_events,
    targets_from,
)
from ingestion.site_models import SiteAdapter
from ingestion.site_pipeline import SiteClient, SiteProcessConfig, crawl_site
from ingestion.site_policy import SourcePolicy
from ingestion.site_repository import SiteRepository

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/local_sites"
INVENTORY = runpy.run_path(str(ROOT / "api/migrations/versions/20260927_0014_local_sites.py"))[
    "SOURCES"
]
COUNTS = {"reads-and-company": 8, "charm-city-books": 9, "philamoca": 55}


@pytest.fixture
def local_inventory(database_url: str, clean_ingestion_tables: None) -> None:
    # The migration owns adapter and market rows. Restore its targets after the shared
    # cleanup fixture; all production configuration is subsequently read from Postgres.
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        for source in INVENTORY:
            connection.execute(
                """INSERT INTO ingest.crawl_target
                (id,source,market_id,source_location,category,enabled,window_days,page_cap)
                VALUES (%s,%s,%s,%s,'arts',true,30,1)""",
                (
                    source["target"],
                    source["source"],
                    source["market"],
                    Jsonb({"kind": "listing_url", "url": source["url"]}),
                ),
            )
        connection.execute("UPDATE ingest.source_adapter SET next_fetch_at=NULL")


class FakeClock:
    def __init__(self) -> None:
        self.now = NOW
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += timedelta(seconds=seconds)


@pytest.mark.usefixtures("local_inventory")
def test_recorded_sources_replay_and_flow_through_existing_pipeline(
    database_url: str,
    graph_config: GraphConfig,  # noqa: F811
) -> None:
    clock = FakeClock()
    requests: Counter[str] = Counter()
    derivations: Counter[str] = Counter()
    contexts = configured_sites(database_url, "stagehand") + configured_sites(database_url, "http")
    contexts = [value for value in contexts if adapter_from(value).source in COUNTS]
    assert {targets_from(value)[0].market_slug for value in contexts} == {
        "philadelphia-pa",
        "baltimore-md",
    }
    config = SiteProcessConfig("https://browser.test", "token", "https://model.test", "key")
    for inventory in contexts:
        adapter = adapter_from(inventory)
        source = adapter.source
        page = (FIXTURES / f"{source}.html").read_text()
        plan = json.loads((FIXTURES / f"{source}-plan.json").read_text())
        first = replay(
            plan,
            page,
            str(targets_from(inventory)[0].source_location["url"]),
            adapter,
            "America/New_York",
        ).events[0]
        assert (
            first["starts_at"]
            == {
                "reads-and-company": "2026-10-07T19:00:00",
                "charm-city-books": "2026-10-03T10:30:00",
                "philamoca": "2026-09-27T19:30:00",  # Show, not 19:00 Doors.
            }[source]
        )
        if adapter.fetch_method == "stagehand":
            with pytest.raises(ValueError, match="no event cards"):
                replay(
                    plan,
                    (FIXTURES / f"{source}-http.html").read_text(),
                    str(targets_from(inventory)[0].source_location["url"]),
                    adapter,
                    "America/New_York",
                )

        def service(
            request: httpx.Request,
            source: str = source,
            plan: dict[str, object] = plan,
            adapter: SiteAdapter = adapter,
            page: str = page,
        ) -> httpx.Response:
            if request.url.host == "model.test":
                derivations[source] += 1
                body = json.loads(request.content)
                assert body["store"] is False
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "model": "deterministic-plan-substitute",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": json.dumps(plan)}],
                            }
                        ],
                    },
                )
            requests[source] += 1
            if request.url.host == "browser.test":
                body = json.loads(request.content)
                assert body["ready_selector"] == adapter.extraction["ready_selector"]
                return httpx.Response(
                    200, json={"html": page, "http_status": 200, "url": body["url"]}
                )
            assert source == "philamoca" and request.url.host == "www.philamoca.org"
            return httpx.Response(200, text=page)

        for run in ("first", "first", "next-night"):
            context = open_site_run(
                database_url, inventory, "local-site-test", run, lambda: clock.now
            )
            with httpx.Client(transport=httpx.MockTransport(service)) as http:
                result = crawl_site(
                    adapter=adapter,
                    targets=targets_from(context),
                    run_id=uuid.UUID(str(context["run_id"])),
                    repository=SiteRepository(database_url),
                    client=SiteClient(
                        http, config, SourcePolicy(database_url, lambda: clock.now, clock.sleep)
                    ),
                    derive=PlanModel(
                        http, config.extraction_api_url, config.extraction_api_key
                    ).derive,
                    clock=lambda: clock.now,
                )
            assert result.appearances == COUNTS[source]
            assert result.partial_reason is None
            context = collect_site_ids(
                database_url, {**context, "appearances": result.appearances, "partial_reason": None}
            )
            stage_site_events(database_url, context, lambda: clock.now)
            context = parse_site_events(database_url, context, lambda: clock.now)
            close_site_run(database_url, context, lambda: clock.now)
        assert requests[source] == 2
        assert derivations[source] == 1

    with psycopg.connect(SiteRepository(database_url).url) as connection:
        assert connection.execute("SELECT count(*) FROM source_listing").fetchone() == (72,)
        assert connection.execute("""SELECT count(*) FROM source_listing
            WHERE extraction_model='deterministic-plan-substitute'
            AND extraction_prompt_version=1""").fetchone() == (72,)

    class RecordedGeocoder:
        def geocode(self, address: str) -> GeocodedVenue:
            if "234 Bridge" in address:
                return GeocodedVenue(
                    "fixture-reads", address, 40.134, -75.517, "Phoenixville", "PA", "US"
                )
            if "426 West Franklin" in address:
                return GeocodedVenue(
                    "fixture-charm", address, 39.295, -76.622, "Baltimore", "MD", "US"
                )
            assert "531 N." in address
            return GeocodedVenue(
                "fixture-philamoca", address, 39.961, -75.158, "Philadelphia", "PA", "US"
            )

    result = geocode_pending(
        repository=GeocodeRepository(database_url),
        ingestion_repository=IngestionRepository(database_url),
        geocoder=RecordedGeocoder(),
        clock=lambda: clock.now,
    )
    assert result.api_calls == 3
    assert (
        canonicalize_pending(
            repository=CanonicalEventRepository(database_url), clock=lambda: clock.now
        ).created
        == 72
    )
    embedding = EmbeddingConfig("openai", "fixture", "https://embedding.test", "key", 10, 100)

    def vectors(request: httpx.Request) -> httpx.Response:
        values = json.loads(request.content)["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [1.0] + [0.0] * 1535}
                    for index, _ in enumerate(values)
                ]
            },
        )

    with EmbeddingClient(embedding, transport=httpx.MockTransport(vectors)) as client:
        assert embed_pending(database_url, client) == 72
        assert embed_pending(database_url, client) == 0
    dedup_pending(database_url, clock=lambda: clock.now)
    assert sum(dedup_pending(database_url, clock=lambda: clock.now).values()) == 0
    for _ in range(2):
        project_to_neo4j(
            database_url, graph_config, airflow_run_id="local-sites", clock=lambda: clock.now
        )
    with GraphDatabase.driver(
        graph_config.uri, auth=(graph_config.user, graph_config.password)
    ) as driver:
        with driver.session(database=graph_config.database) as session:
            rows = session.run(
                """MATCH (s:SourceListing)-[:LISTS]->(:CanonicalEvent)
                WHERE s.source IN $sources RETURN s.source AS source,count(s) AS count""",
                sources=list(COUNTS),
            ).data()
            assert {row["source"]: row["count"] for row in rows} == COUNTS


@pytest.mark.usefixtures("local_inventory")
def test_source_policy_serializes_workers_and_consumes_failed_requests(database_url: str) -> None:
    clock = FakeClock()
    policy = SourcePolicy(database_url, lambda: clock.now, clock.sleep)
    url = "https://www.philamoca.org/"
    with policy.request("philamoca", url, 100) as interval:
        assert interval == 10
        with psycopg.connect(policy.url, autocommit=True) as other:
            assert other.execute(
                "SELECT pg_try_advisory_lock(hashtextextended('philamoca',81))"
            ).fetchone() == (False,)
    with pytest.raises(RuntimeError), policy.request("philamoca", url, 100):
        assert clock.waits == [10]
        raise RuntimeError("recorded transport failure")
    with policy.request("philamoca", url, 100):
        assert clock.waits == [10, 10]
    with policy.request("reads-and-company", "https://www.readsandcompany.com/events", 100):
        assert clock.waits == [10, 10]  # independent source
    with pytest.raises(ValueError, match="outside the reviewed"):
        with policy.request("philamoca", "https://www.philamoca.org/wp-admin/", 100):
            pytest.fail("unreviewed path fetched")
    with psycopg.connect(policy.url) as connection:
        connection.execute("""UPDATE ingest.source_adapter SET access_policy=
            jsonb_set(access_policy,'{status}','\"blocked\"') WHERE source='philamoca'""")
    try:
        with pytest.raises(ValueError, match="no completed access review"):
            with policy.request("philamoca", url, 100):
                pytest.fail("blocked source fetched")
    finally:
        with psycopg.connect(policy.url) as connection:
            connection.execute("""UPDATE ingest.source_adapter SET access_policy=
                jsonb_set(access_policy,'{status}','\"reviewed\"') WHERE source='philamoca'""")


@pytest.mark.usefixtures("local_inventory")
def test_browser_action_replay_uses_source_interval(database_url: str) -> None:
    clock = FakeClock()
    adapter = next(
        value
        for value in SiteRepository(database_url).adapters("stagehand")
        if value.source == "charm-city-books"
    )
    url = "https://www.charmcitybooks.com/events"

    def service(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["steps"][0]["delay_ms"] == 10_000
        return httpx.Response(200, json={"url": url, "http_status": 200, "html": "recorded"})

    with httpx.Client(transport=httpx.MockTransport(service)) as http:
        client = SiteClient(
            http,
            SiteProcessConfig("https://browser.test", "token", "", ""),
            SourcePolicy(database_url, lambda: clock.now, clock.sleep),
        )
        assert client.fetch(
            adapter, url, [{"selector": ".next", "wait_for_selector": ".page-2", "delay_ms": 1}]
        ) == (url, 200, "recorded")
