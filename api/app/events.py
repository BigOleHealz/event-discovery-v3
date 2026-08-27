from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Connection, text

from app.clock import utc_now
from app.database import get_connection

router = APIRouter(prefix="/api/events", tags=["events"])


class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]


class VenueProperties(BaseModel):
    id: str | None
    name: str | None
    formatted_address: str | None
    city: str | None


class EventProperties(BaseModel):
    title: str
    description: str | None
    starts_at: datetime
    ends_at: datetime | None
    timezone: str
    primary_category: str | None
    venue: VenueProperties


class EventFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: PointGeometry
    properties: EventProperties


class EventFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[EventFeature]


EVENT_QUERY = text(
    """
    SELECT
        event.id,
        event.title,
        event.description,
        event.starts_at,
        event.ends_at,
        event.timezone,
        event.primary_category,
        ST_X(event.location::geometry) AS longitude,
        ST_Y(event.location::geometry) AS latitude,
        venue.id AS venue_id,
        venue.name AS venue_name,
        venue.formatted_address,
        venue.city
    FROM canonical_event AS event
    LEFT JOIN venue ON venue.id = event.venue_id
    WHERE event.archived_at IS NULL
      AND event.starts_at >= :current_time
    ORDER BY event.starts_at, event.id
    """
)


@router.get("", response_model=EventFeatureCollection)
def list_events(
    connection: Annotated[Connection, Depends(get_connection)],
    current_time: Annotated[datetime, Depends(utc_now)],
) -> EventFeatureCollection:
    rows = connection.execute(EVENT_QUERY, {"current_time": current_time}).mappings()
    features = [
        EventFeature(
            id=str(row["id"]),
            geometry=PointGeometry(coordinates=(float(row["longitude"]), float(row["latitude"]))),
            properties=EventProperties(
                title=str(row["title"]),
                description=row["description"],
                starts_at=row["starts_at"],
                ends_at=row["ends_at"],
                timezone=str(row["timezone"]),
                primary_category=row["primary_category"],
                venue=VenueProperties(
                    id=str(row["venue_id"]) if row["venue_id"] is not None else None,
                    name=row["venue_name"],
                    formatted_address=row["formatted_address"],
                    city=row["city"],
                ),
            ),
        )
        for row in rows
    ]
    return EventFeatureCollection(features=features)
