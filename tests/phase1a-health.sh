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
curl --fail --silent --output /dev/null "http://${web_address}/"
