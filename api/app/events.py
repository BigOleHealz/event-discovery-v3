from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
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


class GridCellProperties(BaseModel):
    count: int
    top_categories: list[str]


class GridCellFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str
    geometry: PointGeometry
    properties: GridCellProperties


class EventFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[EventFeature | GridCellFeature]


REGISTRATION_LINKS_ADAPTER = TypeAdapter(list[RegistrationLink])
TOP_CATEGORIES_ADAPTER = TypeAdapter(list[str])
INDIVIDUAL_ZOOM_THRESHOLD = 13
MAX_INDIVIDUAL_EVENTS = 500


def grid_cell_size(zoom: int) -> float:
    """Return a roughly 64-pixel-wide grid cell in longitude degrees."""
    return 360.0 / (2.0 ** (zoom + 2))


@dataclass(frozen=True)
class BoundingBox:
    north: float
    south: float
    east: float
    west: float


@dataclass(frozen=True)
class EventFilters:
    starts_after: datetime
    starts_before: datetime | None
    categories: tuple[str, ...]
    time_of_day_start: time | None
    time_of_day_end: time | None


def get_event_filters(
    current_time: Annotated[datetime, Depends(utc_now)],
    starts_after: Annotated[datetime | None, Query()] = None,
    starts_before: Annotated[datetime | None, Query()] = None,
    categories: Annotated[str | None, Query(max_length=1000)] = None,
    time_of_day_start: Annotated[time | None, Query()] = None,
    time_of_day_end: Annotated[time | None, Query()] = None,
) -> EventFilters:
    """Validate event filters and retain the future-events default."""
    for name, value in (("starts_after", starts_after), ("starts_before", starts_before)):
        if value is not None and value.utcoffset() is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{name} must include a UTC offset",
            )
    if starts_after is not None and starts_before is not None and starts_after > starts_before:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="starts_after must be before or equal to starts_before",
        )

    category_values = tuple(
        dict.fromkeys(
            category.strip()
            for category in (categories or "").split(",")
            if category.strip()
        )
    )
    if categories is not None and not category_values:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="categories must contain at least one non-empty category",
        )
    return EventFilters(
        starts_after=starts_after or current_time,
        starts_before=starts_before,
        categories=category_values,
        time_of_day_start=time_of_day_start,
        time_of_day_end=time_of_day_end,
    )


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


BOUNDS_CLAUSE = """
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


FILTER_CLAUSE = """
      AND event.starts_at >= :starts_after
      AND (
          CAST(:starts_before AS timestamptz) IS NULL
          OR event.starts_at <= CAST(:starts_before AS timestamptz)
      )
      AND (
          CAST(:categories AS text[]) IS NULL
          OR event.primary_category = ANY(CAST(:categories AS text[]))
      )
      AND (
          (
              CAST(:time_of_day_start AS time) IS NULL
              AND CAST(:time_of_day_end AS time) IS NULL
          )
          OR (
              CAST(:time_of_day_start AS time) IS NOT NULL
              AND CAST(:time_of_day_end AS time) IS NULL
              AND (event.starts_at AT TIME ZONE event.timezone)::time
                  >= CAST(:time_of_day_start AS time)
          )
          OR (
              CAST(:time_of_day_start AS time) IS NULL
              AND CAST(:time_of_day_end AS time) IS NOT NULL
              AND (event.starts_at AT TIME ZONE event.timezone)::time
                  <= CAST(:time_of_day_end AS time)
          )
          OR (
              CAST(:time_of_day_start AS time) <= CAST(:time_of_day_end AS time)
              AND (event.starts_at AT TIME ZONE event.timezone)::time
                  BETWEEN CAST(:time_of_day_start AS time) AND CAST(:time_of_day_end AS time)
          )
          OR (
              CAST(:time_of_day_start AS time) > CAST(:time_of_day_end AS time)
              AND (
                  (event.starts_at AT TIME ZONE event.timezone)::time
                      >= CAST(:time_of_day_start AS time)
                  OR (event.starts_at AT TIME ZONE event.timezone)::time
                      <= CAST(:time_of_day_end AS time)
              )
          )
      )
"""


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
      {filter_clause}
      {bounds_clause}
    ORDER BY event.starts_at, event.id
    LIMIT {event_limit}
"""

EVENT_QUERY = text(
    EVENT_SELECT.format(
        filter_clause=FILTER_CLAUSE,
        bounds_clause="",
        event_limit=MAX_INDIVIDUAL_EVENTS,
    )
)

BOUNDED_EVENT_QUERY = text(
    EVENT_SELECT.format(
        filter_clause=FILTER_CLAUSE,
        bounds_clause=BOUNDS_CLAUSE,
        event_limit=MAX_INDIVIDUAL_EVENTS,
    )
)

AGGREGATED_EVENT_SELECT = """
    WITH filtered_events AS (
        SELECT
            event.location::geometry AS location,
            event.primary_category
        FROM canonical_event AS event
        WHERE event.archived_at IS NULL
          {filter_clause}
          {bounds_clause}
    ),
    grid_counts AS (
        SELECT
            ST_SnapToGrid(location, :cell_size, :cell_size) AS cell,
            COUNT(*)::integer AS event_count
        FROM filtered_events
        GROUP BY cell
    ),
    category_counts AS (
        SELECT
            ST_SnapToGrid(location, :cell_size, :cell_size) AS cell,
            primary_category,
            COUNT(*)::integer AS category_count
        FROM filtered_events
        WHERE primary_category IS NOT NULL
        GROUP BY cell, primary_category
    ),
    ranked_categories AS (
        SELECT
            cell,
            primary_category,
            category_count,
            ROW_NUMBER() OVER (
                PARTITION BY cell
                ORDER BY category_count DESC, primary_category
            ) AS category_rank
        FROM category_counts
    )
    SELECT
        ST_X(grid_counts.cell) AS longitude,
        ST_Y(grid_counts.cell) AS latitude,
        grid_counts.event_count,
        COALESCE(
            jsonb_agg(
                ranked_categories.primary_category
                ORDER BY ranked_categories.category_rank
            ) FILTER (WHERE ranked_categories.category_rank <= 3),
            '[]'::jsonb
        ) AS top_categories
    FROM grid_counts
    LEFT JOIN ranked_categories ON ranked_categories.cell = grid_counts.cell
    GROUP BY grid_counts.cell, grid_counts.event_count
    ORDER BY latitude, longitude
"""

AGGREGATED_EVENT_QUERY = text(
    AGGREGATED_EVENT_SELECT.format(filter_clause=FILTER_CLAUSE, bounds_clause="")
)
AGGREGATED_BOUNDED_EVENT_QUERY = text(
    AGGREGATED_EVENT_SELECT.format(
        filter_clause=FILTER_CLAUSE,
        bounds_clause=BOUNDS_CLAUSE,
    )
)


@router.get("", response_model=EventFeatureCollection)
def list_events(
    connection: Annotated[Connection, Depends(get_connection)],
    filters: Annotated[EventFilters, Depends(get_event_filters)],
    bounds: Annotated[BoundingBox | None, Depends(get_bounding_box)],
    zoom: Annotated[int, Query(ge=0, le=22)] = INDIVIDUAL_ZOOM_THRESHOLD,
) -> EventFeatureCollection:
    parameters: dict[str, object] = {
        "starts_after": filters.starts_after,
        "starts_before": filters.starts_before,
        "categories": list(filters.categories) or None,
        "time_of_day_start": filters.time_of_day_start,
        "time_of_day_end": filters.time_of_day_end,
    }

    if zoom < INDIVIDUAL_ZOOM_THRESHOLD:
        query = AGGREGATED_EVENT_QUERY
        parameters["cell_size"] = grid_cell_size(zoom)
        if bounds is not None:
            query = AGGREGATED_BOUNDED_EVENT_QUERY
            parameters.update(
                north=bounds.north,
                south=bounds.south,
                east=bounds.east,
                west=bounds.west,
            )
        rows = connection.execute(query, parameters).mappings()
        grid_features: list[EventFeature | GridCellFeature] = [
            GridCellFeature(
                id=(f"cell:{zoom}:{float(row['longitude']):.8f}:{float(row['latitude']):.8f}"),
                geometry=PointGeometry(
                    coordinates=(float(row["longitude"]), float(row["latitude"]))
                ),
                properties=GridCellProperties(
                    count=int(row["event_count"]),
                    top_categories=TOP_CATEGORIES_ADAPTER.validate_python(row["top_categories"]),
                ),
            )
            for row in rows
        ]
        return EventFeatureCollection(features=grid_features)

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
    event_features: list[EventFeature | GridCellFeature] = [
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
    return EventFeatureCollection(features=event_features)
