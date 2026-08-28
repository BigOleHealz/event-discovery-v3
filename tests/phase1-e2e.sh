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
PUBLIC_API_BASE_URL=.
GOOGLE_MAPS_API_KEY=e2e-fixture-key
GOOGLE_MAPS_MAP_ID=DEMO_MAP_ID
export POSTGRES_EXTERNAL_PORT API_EXTERNAL_PORT WEB_EXTERNAL_PORT
export PUBLIC_API_BASE_URL GOOGLE_MAPS_API_KEY GOOGLE_MAPS_MAP_ID

cleanup() {
  docker compose --env-file "$env_file" --file "$compose_file" down
}
trap cleanup EXIT

if ! docker compose --env-file "$env_file" --file "$compose_file" up \
  --detach --build --wait postgres api web; then
  docker compose --env-file "$env_file" --file "$compose_file" ps --all >&2
  docker compose --env-file "$env_file" --file "$compose_file" logs \
    --no-color --tail=200 api web >&2
  exit 1
fi
web_address=$(docker compose --env-file "$env_file" --file "$compose_file" port web "$WEB_PORT")

PLAYWRIGHT_BASE_URL="http://${web_address}" npm --prefix web run test:e2e
