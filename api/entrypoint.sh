#!/bin/sh
set -eu

attempt=1
until alembic upgrade head; do
  if [ "$attempt" -ge "$API_DB_STARTUP_ATTEMPTS" ]; then
    echo "Database migrations failed after $attempt attempts" >&2
    exit 1
  fi
  echo "Database is not ready; retrying migrations in ${API_DB_STARTUP_RETRY_SECONDS}s" >&2
  attempt=$((attempt + 1))
  sleep "$API_DB_STARTUP_RETRY_SECONDS"
done

if [ "${SEED_DATABASE:-false}" = "true" ]; then
  python -m app.seed
fi

exec "$@"
