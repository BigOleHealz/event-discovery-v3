"""Shared connection to the disposable graph projection."""

import os
from functools import lru_cache
from typing import LiteralString

from fastapi import HTTPException
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError


@lru_cache(maxsize=1)
def graph_driver() -> Driver:
    values = {
        key: os.environ.get(key, "").strip()
        for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE")
    }
    if not all(values.values()):
        raise HTTPException(503, "Graph service is not configured")
    return GraphDatabase.driver(
        values["NEO4J_URI"],
        auth=(values["NEO4J_USER"], values["NEO4J_PASSWORD"]),
        connection_timeout=5,
        connection_acquisition_timeout=5,
        max_transaction_retry_time=0,
    )


def query_graph(query: LiteralString, **parameters: object) -> list[dict[str, object]]:
    try:
        with graph_driver().session(database=os.environ["NEO4J_DATABASE"]) as session:
            return session.execute_read(lambda tx: tx.run(query, parameters).data())
    except (DriverError, Neo4jError) as error:
        raise HTTPException(503, "Graph service is temporarily unavailable") from error
