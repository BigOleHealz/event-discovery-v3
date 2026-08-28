from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, HttpUrl, TypeAdapter
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


class RegistrationLink(BaseModel):
    source: str
    url: HttpUrl


class EventProperties(BaseModel):
    title: str
    description: str | None
    starts_at: datetime
    ends_at: datetime | None
    timezone: str
    primary_category: str | None
    venue: VenueProperties
    registration_links: list[RegistrationLink]


class EventFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: PointGeometry
    properties: EventProperties


class EventFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[EventFeature]


REGISTRATION_LINKS_ADAPTER = TypeAdapter(list[RegistrationLink])


@dataclass(frozen=True)
class BoundingBox:
    north: float
    south: float
    east: float
    west: float


def get_bounding_box(
    north: Annotated[float | None, Query(ge=-90, le=90)] = None,
    south: Annotated[float | None, Query(ge=-90, le=90)] = None,
    east: Annotated[float | None, Query(ge=-180, le=180)] = None,
    west: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> BoundingBox | None:
    """Validate an optional map viewport, preserving wrapped longitudes."""
    values = (north, south, east, west)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="north, south, east, and west must be supplied together",
        )
    if north is None or south is None or east is None or west is None:
        raise AssertionError("complete bounds were checked above")
    if north <= south:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="north must be greater than south",
        )
    return BoundingBox(north=north, south=south, east=east, west=west)


EVENT_SELECT = """
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
        venue.city,
        COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'source', listing.source,
                        'url', COALESCE(listing.registration_url, listing.url)
                    )
                    ORDER BY listing.source, listing.id
                )
                FROM source_listing AS listing
                WHERE listing.canonical_event_id = event.id
            ),
            '[]'::jsonb
        ) AS registration_links
    FROM canonical_event AS event
    LEFT JOIN venue ON venue.id = event.venue_id
    WHERE event.archived_at IS NULL
      AND event.starts_at >= :current_time
      {bounds_clause}
    ORDER BY event.starts_at, event.id
"""

EVENT_QUERY = text(EVENT_SELECT.format(bounds_clause=""))

BOUNDED_EVENT_QUERY = text(
    EVENT_SELECT.format(
        bounds_clause="""
      AND (
          (
              :west <= :east
              AND event.location && ST_MakeEnvelope(
                  :west, :south, :east, :north, 4326
              )::geography
          )
          OR
          (
              :west > :east
              AND (
                  event.location && ST_MakeEnvelope(
                      :west, :south, 180, :north, 4326
                  )::geography
                  OR event.location && ST_MakeEnvelope(
                      -180, :south, :east, :north, 4326
                  )::geography
              )
          )
      )
        """
    )
)


@router.get("", response_model=EventFeatureCollection)
def list_events(
    connection: Annotated[Connection, Depends(get_connection)],
    current_time: Annotated[datetime, Depends(utc_now)],
    bounds: Annotated[BoundingBox | None, Depends(get_bounding_box)],
) -> EventFeatureCollection:
    parameters: dict[str, datetime | float] = {"current_time": current_time}
    query = EVENT_QUERY
    if bounds is not None:
        query = BOUNDED_EVENT_QUERY
        parameters.update(
            north=bounds.north,
            south=bounds.south,
            east=bounds.east,
            west=bounds.west,
        )
    rows = connection.execute(query, parameters).mappings()
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
                registration_links=REGISTRATION_LINKS_ADAPTER.validate_python(
                    row["registration_links"]
                ),
            ),
        )
        for row in rows
    ]
    return EventFeatureCollection(features=features)
