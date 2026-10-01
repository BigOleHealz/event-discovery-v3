"""Mutual friendships owned by Postgres; private attendance traversed in Neo4j."""

import os
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from neo4j import ManagedTransaction
from neo4j.exceptions import DriverError, Neo4jError
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import text

from app.auth import Clock, CurrentUser, Database, require_browser_origin
from app.categories import expand_categories
from app.events import (
    BOUNDS_CLAUSE,
    FILTER_CLAUSE,
    BoundingBox,
    EventFilters,
    get_bounding_box,
    get_event_filters,
    grid_cell_size,
)
from app.graph import graph_driver

router = APIRouter(tags=["friends"])


class Person(BaseModel):
    id: UUID
    display_name: str
    avatar_url: str | None


class Friendship(Person):
    status: Literal["incoming", "outgoing", "accepted"]
    created_at: datetime


class FriendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


def projection_lock(connection: Database) -> None:
    # Same lock as the full rebuild: never publish an older social snapshot over a newer one.
    connection.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('project_to_neo4j', 0))")
    )


@router.get("/api/friends")
def friends(user: CurrentUser, connection: Database) -> list[Friendship]:
    rows = connection.execute(
        text("""
        SELECT u.id, coalesce(u.display_name, 'A friend') AS display_name, u.avatar_url,
               f.created_at, CASE WHEN f.accepted_at IS NOT NULL THEN 'accepted'
                   WHEN f.requested_by=:me THEN 'outgoing' ELSE 'incoming' END AS status
        FROM friendship f JOIN app_user u ON u.id = CASE
            WHEN f.user_low=:me THEN f.user_high ELSE f.user_low END
        WHERE :me IN (f.user_low, f.user_high)
        ORDER BY f.created_at DESC, u.id
    """),
        {"me": user.id},
    ).mappings()
    return [Friendship.model_validate(dict(row)) for row in rows]


@router.post("/api/friends", dependencies=[Depends(require_browser_origin)])
def request_friend(
    payload: FriendRequest,
    user: CurrentUser,
    connection: Database,
    now: Clock,
) -> Response:
    projection_lock(connection)
    other = connection.scalar(
        text("""
        SELECT id FROM app_user WHERE email=:email AND google_sub IS NOT NULL AND NOT is_shadow
    """),
        {"email": str(payload.email).casefold()},
    )
    if other is None or other == user.id:
        raise HTTPException(422, "Choose another registered user's email")
    low, high = sorted((user.id, other))
    # A reverse request is still pending until the recipient explicitly accepts.
    connection.execute(
        text("""
        INSERT INTO friendship (user_low, user_high, requested_by, created_at)
        VALUES (:low, :high, :me, :now) ON CONFLICT DO NOTHING
    """),
        {"low": low, "high": high, "me": user.id, "now": now},
    )
    connection.commit()
    return Response(status_code=204)


@router.post("/api/friends/{other_id}/accept", dependencies=[Depends(require_browser_origin)])
def accept_friend(other_id: UUID, user: CurrentUser, connection: Database, now: Clock) -> Response:
    projection_lock(connection)
    low, high = sorted((user.id, other_id))
    row = connection.execute(
        text("""
        UPDATE friendship SET accepted_at=coalesce(accepted_at, :now)
        WHERE user_low=:low AND user_high=:high AND requested_by<>:me RETURNING user_low
    """),
        {"low": low, "high": high, "me": user.id, "now": now},
    ).first()
    if row is None:
        raise HTTPException(404, "Incoming friend request not found")
    connection.commit()
    return Response(status_code=204)


@router.post("/api/friends/{other_id}/remove", dependencies=[Depends(require_browser_origin)])
def remove_friend(other_id: UUID, user: CurrentUser, connection: Database) -> Response:
    projection_lock(connection)
    low, high = sorted((user.id, other_id))
    connection.execute(
        text("DELETE FROM friendship WHERE user_low=:low AND user_high=:high"),
        {"low": low, "high": high},
    )
    connection.commit()
    return Response(status_code=204)


def attending_pairs(connection: Database, user_id: UUID) -> list[dict[str, str]]:
    """Refresh this viewer's social neighbourhood, then traverse it in the same graph transaction.

    The PG lock serializes with friendship changes and scheduled rebuilds. Attendance is
    snapshotted per request, so an accepted invitation appears on the next refresh. No
    email, phone, token, or session is copied into the graph.
    """
    projection_lock(connection)
    friend_ids = [
        str(value)
        for value in connection.scalars(
            text("""
        SELECT CASE WHEN user_low=:me THEN user_high ELSE user_low END
        FROM friendship WHERE :me IN (user_low, user_high) AND accepted_at IS NOT NULL
        ORDER BY user_low, user_high
    """),
            {"me": user_id},
        )
    ]
    # Re-read canonical attendance even if the existing graph has stale/deleted edges.
    attendance = [
        dict(row, created_at=row["created_at"].isoformat())
        for row in connection.execute(
            text("""
        SELECT user_id::text AS friend, canonical_event_id::text AS event, created_at,
               source AS attendance_source
        FROM attendance WHERE user_id=ANY(CAST(:ids AS uuid[])) AND state='attending'
        ORDER BY user_id, canonical_event_id
    """),
            {"ids": friend_ids},
        ).mappings()
    ]

    def refresh(tx: ManagedTransaction) -> list[dict[str, str]]:
        tx.run("MERGE (:User {id: $me})", me=str(user_id)).consume()
        tx.run("MATCH (:User {id: $me})-[r:FRIENDS_WITH]-() DELETE r", me=str(user_id)).consume()
        tx.run(
            """
            UNWIND $ids AS id
            MERGE (friend:User {id: id})
            WITH friend MATCH (me:User {id: $me})
            WITH CASE WHEN me.id < friend.id THEN me ELSE friend END AS a,
                 CASE WHEN me.id < friend.id THEN friend ELSE me END AS b
            MERGE (a)-[:FRIENDS_WITH]->(b)
        """,
            ids=friend_ids,
            me=str(user_id),
        ).consume()
        tx.run(
            "MATCH (u:User)-[r:ATTENDING]->() WHERE u.id IN $ids DELETE r", ids=friend_ids
        ).consume()
        tx.run(
            """
            UNWIND $rows AS row MATCH (u:User {id: row.friend})
            MERGE (e:CanonicalEvent {id: row.event}) MERGE (u)-[r:ATTENDING]->(e)
            SET r.created_at=datetime(row.created_at), r.attendance_source=row.attendance_source
        """,
            rows=attendance,
        ).consume()
        return [
            dict(row)
            for row in tx.run(
                """
            MATCH (:User {id: $me})-[:FRIENDS_WITH]-(u:User)-[:ATTENDING]->(e:CanonicalEvent)
            RETURN DISTINCT u.id AS friend, e.id AS event ORDER BY event, friend
        """,
                me=str(user_id),
            )
        ]

    try:
        with graph_driver().session(database=os.environ["NEO4J_DATABASE"]) as session:
            return session.execute_write(refresh)
    except (DriverError, Neo4jError) as error:
        raise HTTPException(503, "Friends layer is temporarily unavailable") from error


class FriendsEvent(BaseModel):
    event_id: UUID
    friends: list[Person]


class FriendsCell(BaseModel):
    cell_id: str
    event_count: int


class FriendsLayer(BaseModel):
    events: list[FriendsEvent]
    cells: list[FriendsCell]


@router.get("/api/events/friends")
def friends_layer(
    user: CurrentUser,
    connection: Database,
    filters: Annotated[EventFilters, Depends(get_event_filters)],
    bounds: Annotated[BoundingBox | None, Depends(get_bounding_box)],
    zoom: Annotated[int, Query(ge=0, le=22)] = 13,
) -> FriendsLayer:
    if bounds is None:
        raise HTTPException(422, "A viewport is required")
    pairs = attending_pairs(connection, user.id)
    categories = expand_categories(filters.categories)
    rows = (
        connection.execute(
            text(
                """
        SELECT event.id, ST_X(ST_SnapToGrid(event.location::geometry, :size)) AS longitude,
               ST_Y(ST_SnapToGrid(event.location::geometry, :size)) AS latitude
        FROM canonical_event event WHERE event.archived_at IS NULL
        AND event.id=ANY(CAST(:ids AS uuid[]))
    """
                + FILTER_CLAUSE
                + BOUNDS_CLAUSE
                + " ORDER BY event.starts_at, event.id"
            ),
            {
                "ids": list({row["event"] for row in pairs}),
                "size": grid_cell_size(zoom),
                "starts_after": filters.starts_after,
                "starts_before": filters.starts_before,
                "categories": categories,
                "time_of_day_start": filters.time_of_day_start,
                "time_of_day_end": filters.time_of_day_end,
                "north": bounds.north,
                "south": bounds.south,
                "east": bounds.east,
                "west": bounds.west,
            },
        )
        .mappings()
        .all()
    )
    if zoom < 13:
        counts: dict[str, int] = {}
        for row in rows:
            key = f"cell:{zoom}:{float(row['longitude']):.8f}:{float(row['latitude']):.8f}"
            counts[key] = counts.get(key, 0) + 1
        return FriendsLayer(
            events=[], cells=[FriendsCell(cell_id=k, event_count=v) for k, v in counts.items()]
        )
    people = people_by_id(connection, pairs)
    return FriendsLayer(
        cells=[],
        events=[
            FriendsEvent(
                event_id=row["id"],
                friends=[
                    people[pair["friend"]] for pair in pairs if pair["event"] == str(row["id"])
                ],
            )
            for row in rows
        ],
    )


def people_by_id(connection: Database, pairs: list[dict[str, str]]) -> dict[str, Person]:
    rows = connection.execute(
        text("""
        SELECT id, coalesce(display_name, 'A friend') AS display_name, avatar_url
        FROM app_user WHERE id=ANY(CAST(:ids AS uuid[])) ORDER BY id
    """),
        {"ids": list({row["friend"] for row in pairs})},
    ).mappings()
    return {str(row["id"]): Person.model_validate(dict(row)) for row in rows}


@router.get("/api/events/{event_id}/friends")
def event_friends(
    event_id: UUID,
    user: CurrentUser,
    connection: Database,
    now: Clock,
) -> list[Person]:
    if not connection.scalar(
        text("""
        SELECT EXISTS (SELECT 1 FROM canonical_event
            WHERE id=:id AND archived_at IS NULL AND starts_at>=:now)
    """),
        {"id": event_id, "now": now},
    ):
        return []
    pairs = [row for row in attending_pairs(connection, user.id) if row["event"] == str(event_id)]
    return list(people_by_id(connection, pairs).values())
