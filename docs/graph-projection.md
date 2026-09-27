# Graph projection (5a–5c)

Postgres owns all data. The configured Neo4j database is dedicated to this app's
disposable projection: each rebuild replaces **all nodes and relationships**.
Do not store independently maintained data in that database.

Copy the Neo4j settings from `.env.example` into your local environment. Then run
`docker compose up -d --build`. The hourly `project_to_neo4j` DAG runs at minute 30
by default; `NEO4J_PROJECTION_DAG_SCHEDULE` overrides it. Trigger it manually in
Airflow after wiping or restoring the graph. No graph backup is needed.

The projection includes:

| Nodes | Source and identity |
| --- | --- |
| CanonicalEvent | Every `canonical_event`, including past/archived rows; Postgres UUID |
| SourceListing | Every `source_listing`, including unlinked work; Postgres UUID |
| Venue | Every `venue`; Postgres UUID |
| Category | Postgres `category` taxonomy plus unrecognised source labels under Other |
| City | Distinct venue `(city, region, country)` tuples with non-null city; JSON tuple key |

`LISTS`, `HAS_CATEGORY`, `HELD_AT`, and `IN_CITY` follow those rows' references.
`SUBCATEGORY_OF` points from a child category to its parent.
Nullable references create no edge. Null properties are absent in Neo4j. Times are
UTC ISO strings, UUIDs are strings, coordinates are latitude/longitude floats,
and prices are decimal strings to avoid losing precision. Raw payloads, vectors,
and operational metadata remain exclusively in Postgres. `SIMILAR_TO {score}`
connects distinct canonical events using persisted dedup scores. Social nodes and
edges belong to Phase 6.

A Postgres session advisory lock serializes rebuilds, including manual callers.
All reads share one repeatable-read snapshot. Neo4j constraints are recreated if
missing, then deletion and batched inserts commit in one graph transaction. A
failed write leaves the previous data intact. Each Airflow run has one deterministic
`ingest.run` row with success/failure status; retries reset the same row. A crash
between the graph commit and Postgres bookkeeping is repaired by replaying the run.

The 5a migration permits a null `ingest.run.market_id` only when `airflow_dag_id`
is `project_to_neo4j`, because the rebuild covers all markets. Other runs still
require a valid market. Downgrading this migration requires handling any global
run history first; it refuses to silently delete that history or assign it a false market.

The rebuild holds the snapshot in worker memory and replaces the graph in one
transaction. This is deliberately a small-dataset implementation; a larger dataset
would need generation-based publication or another bounded-memory design.

From `airflow/`, run `rtk pytest tests/test_graph.py tests/test_graph_dag.py`.
Tests use disposable PostGIS/pgvector and Neo4j containers. They verify all projected
properties and edges against Postgres, repeat runs, total graph deletion and recovery
through the real DAG, stale-data removal, empty input, and rollback on a graph error.

## Category filtering (5b)

The migration seeds eight palette roots and a small initial hierarchy, including
Music → Jazz → Bebop. `category_alias` maps lowercased, trimmed source labels to
canonical IDs. Add taxonomy and alias rows through migrations, including an alias
for each new category ID. A source label that has no alias is preserved as an
`unmapped:<label>` Category under Other. The original event label stays in Postgres.
This is an initial taxonomy, not an exhaustive genre catalogue.

`GET /api/categories` reads names, immediate parents, root IDs, and aliases from
Neo4j. The sidebar displays the hierarchy independently of the viewport's results.
Selecting Music sends `categories=music`; the API traverses
`(child)-[:SUBCATEGORY_OF*0..]->(parent)` in Cypher and passes the resulting labels
to Postgres alongside the existing date, time, bounds, and zoom filters. Zero hops
includes events labelled with the parent itself. Multiple selections form a union.
The same expansion applies to aggregated cells. A graph outage returns 503 for
category requests; unfiltered events remain available. Unrecognised exact labels
remain filterable before the next projection.

Pins and the legend use the same eight fixed root colours. Descendants resolve
their root from the hierarchy response; cached ancestry preserves colours offline.
After upgrading from 5a, rerun `project_to_neo4j` to populate taxonomy properties and
edges before using the hierarchy. The projection delay remains hourly by default.

From the repository root, `rtk proxy bash tests/phase1-e2e.sh` verifies parent and
child selection through the real Compose API and Neo4j, alongside existing browser
flows. Only Google Maps JavaScript is replaced with a recorded browser fixture.

## Similar events (5c)

`source_listing.dedup_match_id` and `dedup_similarity` are the evidence for each
edge. Resolve both listings' current canonical IDs inside the same Postgres snapshot
as the nodes. Store one edge per unordered canonical pair, oriented by UUID, with
the highest recorded score. Unlinked listings, merged self-pairs, missing scores,
and non-finite/out-of-range scores create no edge. All valid cosine scores in
[-1, 1] are projected; rebuilds remove obsolete edges after a merge or deletion.
This uses the best match retained by dedup, not new all-pairs embedding calls.

`GET /api/events/{id}/similar` traverses edges in either direction and returns up
to five positive-score neighbours, descending by score with UUID tie-breaking.
Postgres supplies the current public details and registration links, filtering
deleted, archived, and past events **before** limiting. These suggestions are
independent of the current map filters. The panel can open a suggestion directly.

Similar-event loading never blocks the selected event's existing details. Empty
results have an explicit message; a graph outage or offline request offers retry,
and reconnecting retries automatically. Recommendations are not cached. Category
and viewport caches retain their existing behavior. Projection freshness remains
hourly; rerun `project_to_neo4j` after deployment to populate similarity edges.
