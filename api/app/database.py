from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine


def database_url_from_env() -> str:
    try:
        return os.environ["DATABASE_URL"]
    except KeyError as error:
        raise RuntimeError("DATABASE_URL is required") from error


def create_database_engine(database_url: str | None = None) -> Engine:
    return create_engine(database_url or database_url_from_env(), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_database_engine()


def get_connection() -> Iterator[Connection]:
    with get_engine().connect() as connection:
        yield connection
