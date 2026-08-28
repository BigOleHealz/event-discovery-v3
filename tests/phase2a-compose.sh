#!/usr/bin/env bash
set -euo pipefail

compose_file=${COMPOSE_FILE:-docker-compose.yml}
env_file=${ENV_FILE:-.env.example}

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

POSTGRES_EXTERNAL_PORT=0
AIRFLOW_EXTERNAL_PORT=0
API_EXTERNAL_PORT=0
export POSTGRES_EXTERNAL_PORT AIRFLOW_EXTERNAL_PORT API_EXTERNAL_PORT

cleanup() {
  docker compose --env-file "$env_file" --file "$compose_file" down
}
trap cleanup EXIT

docker compose --env-file "$env_file" --file "$compose_file" config --quiet
docker compose --env-file "$env_file" --file "$compose_file" up --detach --build --wait \
  postgres api airflow-api-server airflow-scheduler airflow-dag-processor

airflow_address=$(
  docker compose --env-file "$env_file" --file "$compose_file" port \
    airflow-api-server "$AIRFLOW_PORT"
)
health_payload=$(curl --fail --silent "http://${airflow_address}/api/v2/monitor/health")
HEALTH_PAYLOAD="$health_payload" python3 -c \
  'import json, os; payload = json.loads(os.environ["HEALTH_PAYLOAD"]); assert payload["scheduler"]["status"] == "healthy"; assert payload["dag_processor"]["status"] == "healthy"'

ingest_tables=$(
  docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY postgres \
    psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
    --command "SELECT string_agg(tablename, ',' ORDER BY tablename) FROM pg_catalog.pg_tables WHERE schemaname = 'ingest'"
)
test "$ingest_tables" = "event_detail_cache,geocode_cache,page_fetch,rejected_listing,run"

airflow_database=$(
  docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY postgres \
    psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
    --command "SELECT datname FROM pg_database WHERE datname = '$AIRFLOW_DB'"
)
test "$airflow_database" = "$AIRFLOW_DB"

dag_tasks=$(
  docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY \
    airflow-api-server airflow tasks list ingest_eventbrite
)
test "$(printf '%s\n' "$dag_tasks" | sort | tr '\n' ',')" = \
  "close_run,collect_ids,configured_locations,fetch_details,fetch_pages,open_run,parse_listings,"

docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY \
  airflow-api-server python -c \
  'import sys; sys.path.insert(0, "/opt/airflow/dags"); from ingest_eventbrite import ingest_eventbrite; chain = ("configured_locations", "open_run", "fetch_pages", "collect_ids", "fetch_details", "parse_listings", "close_run"); assert all(left in ingest_eventbrite.task_dict[right].upstream_task_ids for left, right in zip(chain, chain[1:]))'

geocode_tasks=$(
  docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY \
    airflow-api-server airflow tasks list geocode_pending
)
test "$(printf '%s\n' "$geocode_tasks" | sort | tr '\n' ',')" = \
  "resolve_pending_venues,upsert_canonical_events,"
