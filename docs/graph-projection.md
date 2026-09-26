# Graph projection (5a)

Postgres owns all data. The configured Neo4j database is dedicated to this app's
disposable projection: each rebuild replaces **all nodes and relationships**.
Do not store independently maintained data in that database.

Copy the Neo4j settings from `.env.example` into your local environment. Then run
`docker compose up -d --build`. The hourly `project_to_neo4j` DAG runs at minute 30
by default; `NEO4J_PROJECTION_DAG_SCHEDULE` overrides it. Trigger it manually in
Airflow after wiping or restoring the graph. No graph backup is needed.

The 5a projection includes:

| Nodes | Source and identity |
| --- | --- |
| CanonicalEvent | Every `canonical_event`, including past/archived rows; Postgres UUID |
| SourceListing | Every `source_listing`, including unlinked work; Postgres UUID |
| Venue | Every `venue`; Postgres UUID |
| Category | Distinct non-null `primary_category` values, retaining exact spelling |
| City | Distinct venue `(city, region, country)` tuples with non-null city; JSON tuple key |

`LISTS`, `HAS_CATEGORY`, `HELD_AT`, and `IN_CITY` follow those rows' references.
Nullable references create no edge. Null properties are absent in Neo4j. Times are
UTC ISO strings, UUIDs are strings, coordinates are latitude/longitude floats,
and prices are decimal strings to avoid losing precision. Raw payloads, vectors,
and operational metadata remain exclusively in Postgres. Category hierarchy and
similarity edges belong to 5b/5c; social nodes and edges belong to Phase 6.

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
