#!/usr/bin/env bash
set -euo pipefail

compose_file=${COMPOSE_FILE:-docker-compose.yml}
env_file=${ENV_FILE:-.env.example}

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

POSTGRES_EXTERNAL_PORT=0
API_EXTERNAL_PORT=0
WEB_EXTERNAL_PORT=0
export POSTGRES_EXTERNAL_PORT API_EXTERNAL_PORT WEB_EXTERNAL_PORT

cleanup() {
  docker compose --env-file "$env_file" --file "$compose_file" down
}
trap cleanup EXIT

docker compose --env-file "$env_file" --file "$compose_file" config --quiet
docker compose --env-file "$env_file" --file "$compose_file" up --detach --build --wait postgres api web

api_address=$(docker compose --env-file "$env_file" --file "$compose_file" port api "$API_PORT")
web_address=$(docker compose --env-file "$env_file" --file "$compose_file" port web "$WEB_PORT")

api_status=$(curl --fail --silent "http://${api_address}/health")
test "$api_status" = '{"status":"ok"}'
events_payload=$(curl --fail --silent "http://${api_address}/api/events")
feature_count=$(
  EVENTS_PAYLOAD="$events_payload" python3 -c \
    'import json, os; payload = json.loads(os.environ["EVENTS_PAYLOAD"]); print(len(payload["features"]))'
)
test "$feature_count" = "20"
curl --fail --silent --output /dev/null "http://${web_address}/"

venue_count=$(
  docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY postgres \
    psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
    --command "SELECT count(*) FROM venue"
)
event_count=$(
  docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY postgres \
    psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align \
    --command "SELECT count(*) FROM canonical_event"
)
test "$venue_count" = "12"
test "$event_count" = "20"
