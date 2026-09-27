"""Rank graph neighbours, then read current public event details from Postgres."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection, text

from app.clock import utc_now
from app.database import get_connection
from app.events import EVENT_SELECT, EventFeature, event_feature
from app.graph import query_graph

router = APIRouter(prefix="/api/events", tags=["events"])


class SimilarEvent(BaseModel):
    event: EventFeature
    score: float


SIMILAR_DETAILS = text(
    EVENT_SELECT.format(
        filter_clause="AND event.id = ANY(CAST(:ids AS uuid[])) AND event.starts_at >= :now",
        bounds_clause="",
        event_limit=5,
    ).replace(
        "ORDER BY event.starts_at, event.id",
        "ORDER BY array_position(CAST(:ids AS uuid[]), event.id)",
    )
)


@router.get("/{event_id}/similar", response_model=list[SimilarEvent])
def similar_events(
    event_id: UUID,
    connection: Annotated[Connection, Depends(get_connection)],
    current_time: Annotated[datetime, Depends(utc_now)],
) -> list[SimilarEvent]:
    exists = connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM canonical_event WHERE id = :id AND archived_at IS NULL)"
        ),
        {"id": event_id},
    )
    if not exists:
        raise HTTPException(404, "Event not found")
    # Store one deterministic direction; discover neighbours from either endpoint.
    rows = query_graph(
        """
        MATCH (:CanonicalEvent {id: $id})-[r:SIMILAR_TO]-(other:CanonicalEvent)
        WHERE other.id <> $id AND r.score > 0 AND r.score <= 1
        RETURN other.id AS id, max(r.score) AS score ORDER BY score DESC, id
    """,
        id=str(event_id),
    )
    scores = {str(row["id"]): float(str(row["score"])) for row in rows}
    if not scores:
        return []
    # Filter archived/deleted/past neighbours before limiting; the hourly graph may lag.
    events = connection.execute(
        SIMILAR_DETAILS, {"ids": list(scores), "now": current_time}
    ).mappings()
    return [SimilarEvent(event=event_feature(row), score=scores[str(row["id"])]) for row in events]
