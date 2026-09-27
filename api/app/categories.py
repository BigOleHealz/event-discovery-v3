"""Read category ancestry from the disposable Neo4j projection."""

import os
from functools import lru_cache
from typing import LiteralString

from fastapi import APIRouter, HTTPException
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel

router = APIRouter(prefix="/api/categories", tags=["categories"])


class Category(BaseModel):
    id: str
    name: str
    parent_id: str | None
    root_id: str
    aliases: list[str]


@lru_cache(maxsize=1)
def graph_driver() -> Driver:
    values = {
        key: os.environ.get(key, "").strip()
        for key in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE")
    }
    if not all(values.values()):
        raise HTTPException(503, "Category service is not configured")
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
        raise HTTPException(503, "Category service is temporarily unavailable") from error


@router.get("", response_model=list[Category])
def list_categories() -> list[Category]:
    rows = query_graph("""
        MATCH (category:Category)-[:SUBCATEGORY_OF*0..]->(root:Category)
        WHERE NOT (root)-[:SUBCATEGORY_OF]->(:Category)
        OPTIONAL MATCH (category)-[:SUBCATEGORY_OF]->(parent:Category)
        RETURN category.id AS id, category.name AS name, parent.id AS parent_id,
               root.id AS root_id, category.aliases AS aliases ORDER BY name, id
    """)
    if any(row["aliases"] is None for row in rows):
        raise HTTPException(503, "Category projection needs rebuilding")
    return [Category.model_validate(row) for row in rows]


def expand_categories(selected: tuple[str, ...]) -> list[str] | None:
    if not selected:
        return None
    requested = sorted({value.strip().lower() for value in selected})
    rows = query_graph(
        """
        UNWIND $selected AS value
        MATCH (parent:Category)
        WHERE parent.id = value OR value IN parent.aliases
        MATCH (child:Category)-[:SUBCATEGORY_OF*0..]->(parent)
        UNWIND child.aliases AS alias
        RETURN DISTINCT alias ORDER BY alias
    """,
        selected=requested,
    )
    # Preserve exact-label filtering for new source labels ahead of the hourly projection.
    return sorted(set(requested) | {str(row["alias"]) for row in rows})
