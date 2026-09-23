#!/usr/bin/env bash
set -euo pipefail

compose_file=${COMPOSE_FILE:-docker-compose.yml}
env_file=${ENV_FILE:-.env.example}

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

# Never seed, review, or remove volumes belonging to the development stack.
export COMPOSE_PROJECT_NAME="event-discovery-e2e-$$"

POSTGRES_EXTERNAL_PORT=0
API_EXTERNAL_PORT=0
WEB_EXTERNAL_PORT=0
PUBLIC_API_BASE_URL=.
GOOGLE_MAPS_API_KEY=e2e-fixture-key
GOOGLE_MAPS_MAP_ID=DEMO_MAP_ID
export POSTGRES_EXTERNAL_PORT API_EXTERNAL_PORT WEB_EXTERNAL_PORT
export PUBLIC_API_BASE_URL GOOGLE_MAPS_API_KEY GOOGLE_MAPS_MAP_ID
export ADMIN_REVIEW_TOKEN=phase4f-fixture-token
export ADMIN_REVIEW_USER_ID=4f000000-0000-0000-0000-000000000001

cleanup() {
  docker compose --env-file "$env_file" --file "$compose_file" down --volumes
}
trap cleanup EXIT

if ! docker compose --env-file "$env_file" --file "$compose_file" up \
  --detach --build --wait postgres api web; then
  docker compose --env-file "$env_file" --file "$compose_file" ps --all >&2
  docker compose --env-file "$env_file" --file "$compose_file" logs \
    --no-color --tail=200 api web >&2
  exit 1
fi
docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY postgres \
  psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
  < tests/phase4e-seed.sql
docker compose --env-file "$env_file" --file "$compose_file" exec --no-TTY postgres \
  psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
  < tests/phase4f-seed.sql
web_address=$(docker compose --env-file "$env_file" --file "$compose_file" port web "$WEB_PORT")

PLAYWRIGHT_BASE_URL="http://${web_address}" npm --prefix web run test:e2e
