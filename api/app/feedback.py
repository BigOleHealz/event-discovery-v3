from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from app.auth import Clock, CurrentUser, Database, require_browser_origin

router = APIRouter(prefix="/api/attendance", tags=["feedback"])


class FeedbackInput(BaseModel):
    rating: int = Field(ge=1, le=5, strict=True)
    feedback_text: str | None = Field(default=None, max_length=2000)

    @field_validator("feedback_text")
    @classmethod
    def trimmed_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class Feedback(BaseModel):
    attendance_id: UUID
    canonical_event_id: UUID
    event_title: str
    starts_at: datetime
    timezone: str
    requested_at: datetime
    rating: int | None
    feedback_text: str | None
    feedback_at: datetime | None


FEEDBACK_SELECT = """
    SELECT a.id AS attendance_id, a.canonical_event_id, e.title AS event_title,
           e.starts_at, e.timezone, r.requested_at, a.rating, a.feedback_text, a.feedback_at
    FROM attendance a JOIN event_feedback_request r ON r.attendance_id=a.id
    JOIN canonical_event e ON e.id=a.canonical_event_id
"""
# Recheck dates when reading/writing: an event may have been rescheduled after the DAG ran.
ELAPSED_EVENT = """
    (COALESCE(e.ends_at, e.starts_at) AT TIME ZONE e.timezone)::date
        < (CAST(:now AS timestamptz) AT TIME ZONE e.timezone)::date
"""


@router.get("/feedback")
def feedback_requests(
    user: CurrentUser,
    connection: Database,
    now: Clock,
    completed: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Feedback]:
    rows = (
        connection.execute(
            text(
                FEEDBACK_SELECT
                + f"""
        WHERE a.user_id=:user AND a.state='attended' AND {ELAPSED_EVENT}
            AND (a.feedback_at IS NOT NULL)=:completed
        ORDER BY r.requested_at DESC, a.id LIMIT :limit OFFSET :offset
    """
            ),
            {"user": user.id, "now": now, "completed": completed, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return [Feedback.model_validate(dict(row)) for row in rows]


@router.post("/{attendance_id}/feedback", dependencies=[Depends(require_browser_origin)])
def submit_feedback(
    attendance_id: UUID,
    payload: FeedbackInput,
    user: CurrentUser,
    connection: Database,
    now: Clock,
) -> Feedback:
    # Lock only this attendance; serialize repeated submissions without locking the inbox.
    row = (
        connection.execute(
            text(f"""
        SELECT a.state, {ELAPSED_EVENT} AS elapsed FROM attendance a
        JOIN canonical_event e ON e.id=a.canonical_event_id
        JOIN event_feedback_request r ON r.attendance_id=a.id
        WHERE a.id=:id AND a.user_id=:user FOR UPDATE OF a
    """),
            {"id": attendance_id, "user": user.id, "now": now},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise HTTPException(404, "Feedback request not found")
    if row["state"] != "attended" or not row["elapsed"]:
        raise HTTPException(409, "Feedback is only available after the event")
    connection.execute(
        text("""
        UPDATE attendance SET rating=:rating, feedback_text=:feedback_text, feedback_at=:now
        WHERE id=:id AND (rating IS DISTINCT FROM :rating
            OR feedback_text IS DISTINCT FROM :feedback_text OR feedback_at IS NULL)
    """),
        {
            "id": attendance_id,
            "rating": payload.rating,
            "feedback_text": payload.feedback_text,
            "now": now,
        },
    )
    saved = (
        connection.execute(text(FEEDBACK_SELECT + " WHERE a.id=:id"), {"id": attendance_id})
        .mappings()
        .one()
    )
    result = Feedback.model_validate(dict(saved))
    connection.commit()
    return result
