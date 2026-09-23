"""Admin review of ambiguous pairs; decisions and merges commit together."""

import os
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.admin import require_admin
from app.clock import utc_now
from app.database import get_connection

router = APIRouter(prefix="/api/admin/dedup", tags=["admin"])
Admin = Annotated[UUID, Depends(require_admin)]
Database = Annotated[Connection, Depends(get_connection)]
Decision = Literal["merged", "distinct", "skipped"]


class ListingSnapshot(BaseModel):
    listing_id: UUID
    title: str
    description: str | None
    starts_at: datetime
    ends_at: datetime | None
    timezone: str
    venue: str | None
    address: str | None
    source: str
    url: str


class ReviewPair(BaseModel):
    id: UUID
    similarity_score: float
    time_delta_minutes: int
    distance_meters: int
    snapshot_a: ListingSnapshot
    snapshot_b: ListingSnapshot
    event_a_id: UUID
    event_b_id: UUID


class ReviewQueue(BaseModel):
    pending: int
    pair: ReviewPair | None


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Decision
    event_a_id: UUID
    event_b_id: UUID


class DecisionResult(BaseModel):
    id: UUID
    status: Decision
    decided_by: UUID
    decided_at: datetime


def check_reviewer(connection: Connection, reviewer: UUID) -> None:
    if not connection.scalar(text("SELECT 1 FROM app_user WHERE id = :id"), {"id": reviewer}):
        raise HTTPException(503, "Admin reviewer does not exist; provision the reviewer first")


@router.get("", response_model=ReviewQueue)
def review_queue(admin: Admin, connection: Database, response: Response) -> ReviewQueue:
    response.headers["Cache-Control"] = "no-store"
    check_reviewer(connection, admin)
    # One statement keeps the count and next pair consistent with concurrent decisions.
    row = connection.execute(text("""
        SELECT (SELECT count(*) FROM dedup_review WHERE status = 'pending') AS pending,
               next_pair.*
        FROM (SELECT 1) singleton
        LEFT JOIN LATERAL (
            SELECT review.*, a.canonical_event_id AS event_a_id,
                   b.canonical_event_id AS event_b_id
            FROM dedup_review review
            JOIN source_listing a ON a.id = review.listing_a_id
            JOIN source_listing b ON b.id = review.listing_b_id
            WHERE review.status = 'pending'
            ORDER BY review.created_at, review.id LIMIT 1
        ) next_pair ON true
    """)).mappings().one()
    return ReviewQueue(
        pending=row["pending"], pair=ReviewPair.model_validate(row) if row["id"] else None
    )


def merge_events(connection: Connection, event_a: UUID, event_b: UUID) -> None:
    if event_a == event_b:
        return
    # A previous human Distinct label must survive transitive merges as well.
    conflict = connection.scalar(text("""
        SELECT 1 FROM dedup_review r
        JOIN source_listing a ON a.id = r.listing_a_id
        JOIN source_listing b ON b.id = r.listing_b_id
        WHERE r.status = 'distinct'
          AND a.canonical_event_id IN (:a, :b) AND b.canonical_event_id IN (:a, :b)
        LIMIT 1
    """), {"a": event_a, "b": event_b})
    if conflict:
        raise HTTPException(409, "Merge conflicts with a previous Distinct decision")
    priority = [source.strip() for source in os.environ.get(
        "DEDUP_SOURCE_PRIORITY", "eventbrite,meetup"
    ).split(",") if source.strip()]
    rows = connection.execute(text("""
        SELECT canonical_event_id, source FROM source_listing
        WHERE canonical_event_id IN (:a, :b) ORDER BY canonical_event_id, id FOR UPDATE
    """), {"a": event_a, "b": event_b}).all()
    winner = min(rows, key=lambda row: (
        priority.index(row.source) if row.source in priority else len(priority),
        str(row.canonical_event_id),
    )).canonical_event_id
    loser = event_b if winner == event_a else event_a
    connection.execute(text("""
        UPDATE source_listing SET canonical_event_id = :winner WHERE canonical_event_id = :loser
    """), {"winner": winner, "loser": loser})
    # Foreign keys deliberately reject deleting events with dependent user data.
    connection.execute(text("DELETE FROM canonical_event WHERE id = :id"), {"id": loser})


@router.post("/{review_id}/decision", response_model=DecisionResult)
def decide_pair(
    review_id: UUID,
    decision: ReviewDecision,
    admin: Admin,
    connection: Database,
    response: Response,
    now: Annotated[datetime, Depends(utc_now)],
) -> DecisionResult:
    response.headers["Cache-Control"] = "no-store"
    try:
        with connection.begin():
            # Same lock as 4d; no worker can merge the other direction mid-decision.
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtextextended('dedup_pending', 0))"
            ))
            check_reviewer(connection, admin)
            row = connection.execute(text("""
                SELECT r.*, a.canonical_event_id AS event_a_id, b.canonical_event_id AS event_b_id
                FROM dedup_review r
                JOIN source_listing a ON a.id = r.listing_a_id
                JOIN source_listing b ON b.id = r.listing_b_id
                WHERE r.id = :id FOR UPDATE OF r, a, b
            """), {"id": review_id}).mappings().first()
            if row is None:
                raise HTTPException(404, "Review pair not found")
            if row["status"] != "pending":
                if row["status"] == decision.status:
                    return DecisionResult.model_validate(row)
                raise HTTPException(409, "This pair has already been decided; refresh the queue")
            if (row["event_a_id"], row["event_b_id"]) != (
                decision.event_a_id, decision.event_b_id
            ):
                raise HTTPException(409, "The events changed; refresh the queue before deciding")
            if decision.status == "merged":
                merge_events(connection, row["event_a_id"], row["event_b_id"])
            elif decision.status == "distinct" and row["event_a_id"] == row["event_b_id"]:
                raise HTTPException(409, "These listings already share an event; skip this pair")
            connection.execute(text("""
                UPDATE dedup_review SET status = :status, decided_by = :admin, decided_at = :now
                WHERE id = :id
            """), {"id": review_id, "status": decision.status, "admin": admin, "now": now})
            return DecisionResult(
                id=review_id, status=decision.status, decided_by=admin, decided_at=now
            )
    except IntegrityError as error:
        raise HTTPException(
            409, "Merge blocked by dependent data; both events were preserved"
        ) from error
