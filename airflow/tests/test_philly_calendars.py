from __future__ import annotations

import hashlib
import json
import runpy
import uuid
from collections import Counter
from dataclasses import replace
from pathlib import Path

import httpx
import psycopg
import pytest
from neo4j import GraphDatabase
from psycopg.types.json import Jsonb
from test_graph import graph_config  # noqa: F401
from test_local_sites import FakeClock

from ingestion import site_jsonld
from ingestion.canonicalization import CanonicalEventRepository, canonicalize_pending
from ingestion.database import IngestionRepository
from ingestion.dedup import dedup_pending
from ingestion.embeddings import EmbeddingClient, EmbeddingConfig, embed_pending
from ingestion.geocode_pipeline import geocode_pending
from ingestion.geocode_repository import GeocodeRepository
from ingestion.geocoding import GeocodedVenue
from ingestion.graph import GraphConfig, project_to_neo4j
from ingestion.site_extraction import DerivedPlan, PlanModel, replay
from ingestion.site_jobs import (
    adapter_from,
    close_site_run,
    collect_site_ids,
    configured_sites,
    open_site_run,
    parse_site_events,
    targets_from,
)
from ingestion.site_models import EVENT_SCHEMA, SiteAdapter
from ingestion.site_pipeline import SiteClient, SiteProcessConfig, crawl_site
from ingestion.site_policy import SourcePolicy
from ingestion.site_repository import SiteRepository

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/philly_calendars"
INVENTORY = runpy.run_path(str(ROOT / "api/migrations/versions/20260928_0016_philly_calendars.py"))[
    "SOURCES"
]


def plan() -> dict[str, object]:
    return json.loads((FIXTURES / "plan.json").read_text())


def adapter() -> SiteAdapter:
    return SiteAdapter(
        "test",
        "http",
        {
            "format": "jsonld",
            "instruction": "Event facts",
            "schema": EVENT_SCHEMA,
            "exclude_midnight": True,
        },
        {"kind": "next_link"},
        "fixture",
        1,
    )


def page(nodes: object, next_link: bool = False) -> str:
    return (
        '<script type="application/ld+json">'
        + json.dumps(nodes)
        + "</script>"
        + ('<a class="tribe-events-c-nav__next" href="?page=2">Next</a>' if next_link else "")
    )


def event() -> dict[str, object]:
    return {
        "@type": "Event",
        "name": "A workshop",
        "url": "/event/workshop/",
        "startDate": "2026-10-02T17:00:00-04:00",
        "endDate": "2026-10-02T20:00:00-04:00",
        "location": {
            "name": "Venue",
            "address": {"streetAddress": "10 Main St", "addressLocality": "Philadelphia"},
        },
    }


def test_jsonld_graph_mapping_and_validation() -> None:
    result = replay(
        plan(), page({"@graph": [event()]}), "https://source.test/", adapter(), "America/New_York"
    )
    assert result.events[0]["url"] == "https://source.test/event/workshop/"
    assert result.events[0]["starts_at"] == "2026-10-02T17:00:00-04:00"
    assert result.events[0]["description"] is None
    assert not result.skipped
    for bad in (
        {**event(), "startDate": "broken"},
        {**event(), "name": None},
        {**event(), "endDate": "2026-01-01T12:00:00-05:00"},
    ):
        # A missing time is intentionally excluded; malformed timestamp with T must fail.
        if bad.get("startDate") == "broken":
            bad["startDate"] = "brokenTtime"
        with pytest.raises((ValueError, TypeError)):
            replay(plan(), page(bad), "https://source.test/", adapter(), "America/New_York")
    with pytest.raises(ValueError, match="no schema.org"):
        replay(plan(), "<html>Redesigned calendar</html>", "https://source.test", adapter(), "UTC")
    with pytest.raises(json.JSONDecodeError):
        site_jsonld.event_nodes('<script type="application/ld+json">broken</script>')


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"location": False}, "missing_street_address"),
        ({"startDate": "2026-10-02"}, "no_explicit_time"),
        ({"startDate": "2026-10-02T00:00:00-04:00"}, "midnight_or_all_day"),
        ({"eventStatus": "https://schema.org/EventCancelled"}, "cancelled_or_postponed"),
        ({"eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode"}, "online_only"),
    ],
)
def test_structured_exclusions_are_reported(changes: dict[str, object], reason: str) -> None:
    result = replay(plan(), page({**event(), **changes}), "https://source.test", adapter(), "UTC")
    assert not result.events
    assert result.skipped == [{"url": "/event/workshop/", "reason": reason}]


def test_venue_fallback_never_overrides_offsite_location() -> None:
    source = replace(
        adapter(),
        extraction={
            **adapter().extraction,
            "venue": {"venue_address": "719 Catharine Street", "venue_city": "Philadelphia"},
        },
    )
    result = replay(
        plan(),
        page(
            [
                {**event(), "location": False},
                event(),
                {**event(), "location": {"name": "Other venue"}},
            ]
        ),
        "https://source.test",
        source,
        "UTC",
    )
    assert [e["venue_address"] for e in result.events] == [
        "719 Catharine Street, Philadelphia",
        "10 Main St, Philadelphia",
    ]
    assert len(result.skipped) == 1
    broken = plan()
    broken["fields"]["venue_address"] = "/nonexistent"  # type: ignore[index]
    with pytest.raises(ValueError, match="missed an available"):
        replay(broken, page(event()), "https://source.test", source, "UTC")


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_recorded_calendars_cache_paginate_rate_limit_and_ingest(
    database_url: str,
    graph_config: GraphConfig,  # noqa: F811
) -> None:
    clock = FakeClock()
    repository = SiteRepository(database_url)
    with psycopg.connect(repository.url) as connection:
        for source in INVENTORY:
            connection.execute(
                """INSERT INTO ingest.crawl_target
                (id,source,market_id,source_location,category,enabled,window_days,page_cap)
                VALUES (%s,%s,%s,%s,%s,true,30,2)""",
                (
                    source["target"],
                    source["source"],
                    "8a7a04d3-7fb6-4cdb-a3d7-e5f08cf48bed",
                    Jsonb({"kind": "listing_url", "url": source["url"]}),
                    source["category"],
                ),
            )
        connection.execute("UPDATE ingest.source_adapter SET next_fetch_at=NULL")
    contexts = [
        c
        for c in configured_sites(database_url, "http")
        + configured_sites(database_url, "stagehand")
        if adapter_from(c).source in {s["source"] for s in INVENTORY}
    ]
    assert len(contexts) == 3
    total = 0
    for inventory in contexts:
        source = adapter_from(inventory)
        calls: Counter[str] = Counter()
        expected: set[str] = set()
        skips = 0
        for number in (1, 2):
            result = replay(
                plan(),
                (FIXTURES / f"{source.source}-{number}.html").read_text(),
                str(targets_from(inventory)[0].source_location["url"]),
                source,
                "America/New_York",
            )
            expected.update(str(e["source_event_id"]) for e in result.events)
            skips += len(result.skipped)
        assert len(expected) > 5
        total += len(expected)

        def service(
            request: httpx.Request, calls: Counter[str] = calls, source: SiteAdapter = source
        ) -> httpx.Response:
            if request.url.host == "model.test":
                calls["model"] += 1
                document = json.loads(json.loads(request.content)["input"])["html"]
                assert "startDate" in document  # JSON-LD survives model input preparation.
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "model": "fixture-plan-model",
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": json.dumps(plan())}],
                            }
                        ],
                    },
                )
            browser = request.url.host == "browser.test"
            actual = httpx.URL(json.loads(request.content)["url"]) if browser else request.url
            number = 2 if "/page/2/" in actual.path else 1
            assert actual.path.endswith("/")
            calls["fetch"] += 1
            html = (FIXTURES / f"{source.source}-{number}.html").read_text()
            return (
                httpx.Response(200, json={"url": str(actual), "html": html, "http_status": 200})
                if browser
                else httpx.Response(200, text=html)
            )

        for execution in ("first", "first", "second"):
            context = open_site_run(
                database_url, inventory, "philly-test", execution, lambda: clock.now
            )
            with httpx.Client(transport=httpx.MockTransport(service)) as http:
                result = crawl_site(
                    adapter=source,
                    targets=targets_from(context),
                    run_id=uuid.UUID(str(context["run_id"])),
                    repository=repository,
                    client=SiteClient(
                        http,
                        SiteProcessConfig(
                            "https://browser.test", "key", "https://model.test", "key"
                        ),
                        SourcePolicy(database_url, lambda: clock.now, clock.sleep),
                    ),
                    derive=PlanModel(http, "https://model.test", "key").derive,
                    clock=lambda: clock.now,
                )
            assert result.pages == 2
            assert result.partial_reason == "page cap reached"
            context = collect_site_ids(
                database_url,
                {
                    **context,
                    "appearances": result.appearances,
                    "partial_reason": result.partial_reason,
                },
            )
            context = parse_site_events(database_url, context, lambda: clock.now)
            close_site_run(database_url, context, lambda: clock.now)
            with psycopg.connect(repository.url) as connection:
                assert connection.execute(
                    """SELECT sum(jsonb_array_length(extraction_skips))
                    FROM ingest.site_page p JOIN ingest.page_fetch f ON f.id=p.page_fetch_id
                    WHERE f.run_id=%s""",
                    (context["run_id"],),
                ).fetchone() == (skips,)
        assert calls == {"model": 1, "fetch": 4}
        assert 10 in clock.waits
        # A cached bad mapping is invalidated and re-derived once on a new snapshot.
        with psycopg.connect(repository.url) as connection:
            connection.execute(
                "UPDATE ingest.extraction_plan SET plan='{}' WHERE source=%s", (source.source,)
            )
        context = open_site_run(database_url, inventory, "philly-test", "third", lambda: clock.now)
        with httpx.Client(transport=httpx.MockTransport(service)) as http:
            crawl_site(
                adapter=source,
                targets=targets_from(context),
                run_id=uuid.UUID(str(context["run_id"])),
                repository=repository,
                client=SiteClient(http, SiteProcessConfig("https://browser.test", "key", "", "")),
                derive=PlanModel(http, "https://model.test", "key").derive,
                clock=lambda: clock.now,
            )
        assert calls["model"] == 2
    with psycopg.connect(repository.url) as connection:
        assert connection.execute("SELECT count(*) FROM source_listing").fetchone() == (total,)
        assert connection.execute("""SELECT count(*) FROM source_listing
            WHERE extraction_model='fixture-plan-model' AND extraction_prompt_version=1
            AND raw_payload->'event'->>'starts_at' IS NOT NULL
            AND raw_payload->'event'->>'venue_address' IS NOT NULL""").fetchone() == (total,)

    # Exercise downstream services with one real listing per source; the complete
    # recorded inventory above already verifies extraction, staging and retry counts.
    with psycopg.connect(repository.url) as connection:
        connection.execute("""DELETE FROM source_listing WHERE id NOT IN (
            SELECT DISTINCT ON (source) id FROM source_listing ORDER BY source,source_event_id
        )""")
    downstream_total = len(contexts)

    class FixtureGeocoder:
        def geocode(self, address: str) -> GeocodedVenue:
            return GeocodedVenue(
                hashlib.sha256(address.encode()).hexdigest(),
                address,
                39.95,
                -75.16,
                "Philadelphia",
                "PA",
                "US",
            )

    geocode_pending(
        repository=GeocodeRepository(database_url),
        ingestion_repository=IngestionRepository(database_url),
        geocoder=FixtureGeocoder(),
        clock=lambda: clock.now,
    )
    assert (
        canonicalize_pending(
            repository=CanonicalEventRepository(database_url), clock=lambda: clock.now
        ).created
        == downstream_total
    )
    assert (
        canonicalize_pending(
            repository=CanonicalEventRepository(database_url), clock=lambda: clock.now
        ).created
        == 0
    )

    def vectors(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0] + [0.0] * 1535} for i in range(len(inputs))
                ]
            },
        )

    config = EmbeddingConfig("openai", "fixture", "https://embedding.test", "key", 10, 100)
    with EmbeddingClient(config, transport=httpx.MockTransport(vectors)) as client:
        assert embed_pending(database_url, client) == downstream_total
        assert embed_pending(database_url, client) == 0
    dedup_pending(database_url, clock=lambda: clock.now)
    assert sum(dedup_pending(database_url, clock=lambda: clock.now).values()) == 0
    for _ in range(2):
        project_to_neo4j(
            database_url, graph_config, airflow_run_id="philly-fixtures", clock=lambda: clock.now
        )
    with GraphDatabase.driver(
        graph_config.uri, auth=(graph_config.user, graph_config.password)
    ) as driver:
        with driver.session(database=graph_config.database) as session:
            row = session.run(
                """MATCH (s:SourceListing)-[:LISTS]->(:CanonicalEvent)
                WHERE s.source IN $sources RETURN count(s) AS count""",
                sources=[s["source"] for s in INVENTORY],
            ).single()
            assert row is not None and row["count"] == downstream_total


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_excluded_page_does_not_end_pagination(database_url: str) -> None:
    from test_sites import seed

    source = seed(database_url, replace(adapter(), source="fixture-site"))
    inventory = next(
        c for c in configured_sites(database_url, "http") if adapter_from(c).source == source.source
    )
    context = open_site_run(
        database_url, inventory, "structured-test", "excluded", lambda: FakeClock().now
    )
    requests = []

    def service(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            200,
            text=page(
                event() if request.url.query else {**event(), "location": False},
                next_link=not request.url.query,
            ),
        )

    with httpx.Client(transport=httpx.MockTransport(service)) as http:
        result = crawl_site(
            adapter=source,
            targets=targets_from(context),
            run_id=uuid.UUID(str(context["run_id"])),
            repository=SiteRepository(database_url),
            client=SiteClient(http, SiteProcessConfig("https://browser.test", "key", "", "")),
            derive=lambda *_: DerivedPlan(plan(), "fixture-model"),
            clock=lambda: FakeClock().now,
        )
    assert result.pages == 2 and result.appearances == 1
    assert len(requests) == 2
