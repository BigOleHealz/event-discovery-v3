#!/bin/sh
set -eu

alembic upgrade head

if [ "${SEED_DATABASE:-false}" = "true" ]; then
  python -m app.seed
fi

exec "$@"
