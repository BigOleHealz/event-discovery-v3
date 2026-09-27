"""Read category ancestry from the disposable Neo4j projection."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.graph import query_graph

router = APIRouter(prefix="/api/categories", tags=["categories"])


class Category(BaseModel):
    id: str
    name: str
    parent_id: str | None
    root_id: str
    aliases: list[str]


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
