"""Resolve embedded listings after an exact, occurrence-specific geographic filter."""

from __future__ import annotations

import math
import uuid
from typing import Literal

import psycopg

from ingestion.clock import Clock

Classification = Literal["same", "review", "distinct"]

# MATERIALIZED is an optimization fence: even an HNSW plan must not rank globally
# and filter its top ten afterwards. Distance is evaluated only on eligible rows.
CANDIDATE_SQL = """
    WITH incoming AS MATERIALIZED (
        SELECT sl.embedding, sl.canonical_event_id, event.starts_at, event.location
        FROM source_listing sl
        JOIN canonical_event event ON event.id = sl.canonical_event_id
        WHERE sl.id = %(listing_id)s
    ), eligible AS MATERIALIZED (
        SELECT existing.id, existing.canonical_event_id, existing.embedding
        FROM source_listing existing
        JOIN canonical_event event ON event.id = existing.canonical_event_id
        CROSS JOIN incoming
        WHERE existing.embedding IS NOT NULL
          AND existing.canonical_event_id <> incoming.canonical_event_id
          AND event.starts_at BETWEEN incoming.starts_at - INTERVAL '90 minutes'
                                  AND incoming.starts_at + INTERVAL '90 minutes'
          AND ST_DWithin(event.location, incoming.location, 500)
          AND NOT EXISTS (
              SELECT 1 FROM dedup_review review
              JOIN source_listing a ON a.id = review.listing_a_id
              JOIN source_listing b ON b.id = review.listing_b_id
              WHERE review.status IN ('pending', 'distinct', 'skipped')
                AND a.canonical_event_id IN (event.id, incoming.canonical_event_id)
                AND b.canonical_event_id IN (event.id, incoming.canonical_event_id)
          )
    )
    SELECT eligible.id, eligible.canonical_event_id,
           1 - (eligible.embedding <=> incoming.embedding) AS similarity
    FROM eligible CROSS JOIN incoming
    ORDER BY eligible.embedding <=> incoming.embedding, eligible.id
    LIMIT 10
"""


def classify(similarity: float | None) -> Classification:
    """Classify similarity (not distance); non-finite scores cannot authorize a merge."""
    if similarity is None or not math.isfinite(similarity):
        return "distinct"
    if similarity >= 0.88:
        return "same"
    if similarity >= 0.75:
        return "review"
    return "distinct"


def dedup_pending(
    database_url: str,
    *,
    clock: Clock,
    source_priority: tuple[str, ...] = ("eventbrite", "meetup"),
) -> dict[str, int]:
    """Resolve pending rows atomically, retaining separate canonicals for review.

    Canonicalization has already created each listing's safe standalone occurrence.
    Failed transactions leave it pending. The transaction advisory lock serializes
    overlapping dedup workers so they cannot merge A into B and B into A.
    """
    counts = {"same": 0, "review": 0, "distinct": 0}
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url) as connection:
        while True:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('dedup_pending', 0))"
                )
                row = connection.execute("""
                    SELECT sl.id, sl.canonical_event_id, sl.ingestion_run_id
                    FROM source_listing sl
                    JOIN canonical_event event ON event.id = sl.canonical_event_id
                    WHERE sl.dedup_state = 'pending' AND sl.embedding IS NOT NULL
                    ORDER BY sl.first_seen_at, sl.id
                    LIMIT 1
                    FOR UPDATE OF sl, event
                """).fetchone()
                if row is None:
                    return counts
                listing_id, event_id, run_id = row
                matches = connection.execute(CANDIDATE_SQL, {"listing_id": listing_id}).fetchall()
                match_id, matched_event_id, similarity = (
                    matches[0] if matches else (None, None, None)
                )
                outcome = classify(similarity)
                if outcome == "same":
                    _merge(connection, event_id, matched_event_id, source_priority)
                    connection.execute(
                        """UPDATE ingest.run
                           SET events_deduped = COALESCE(events_deduped, 0) + 1
                           WHERE id = %s""",
                        (run_id,),
                    )
                connection.execute(
                    """UPDATE source_listing
                       SET dedup_state = %s, dedup_match_id = %s,
                           dedup_similarity = %s, dedup_checked_at = %s
                       WHERE id = %s""",
                    (outcome, match_id, similarity, clock(), listing_id),
                )
            counts[outcome] += 1


def _merge(
    connection: psycopg.Connection[tuple[object, ...]],
    event_id: uuid.UUID,
    matched_event_id: uuid.UUID,
    priority: tuple[str, ...],
) -> None:
    # Keep the canonical with the highest-priority source, regardless of arrival
    # order. Unknown sources follow configured ones; UUID breaks equal-priority ties.
    rows = connection.execute(
        """SELECT sl.canonical_event_id, sl.source
           FROM source_listing sl
           WHERE sl.canonical_event_id IN (%s, %s)
           ORDER BY sl.canonical_event_id, sl.id FOR UPDATE""",
        (event_id, matched_event_id),
    ).fetchall()
    winner = min(
        rows,
        key=lambda row: (
            priority.index(str(row[1])) if str(row[1]) in priority else len(priority),
            str(row[0]),
        ),
    )[0]
    loser = matched_event_id if winner == event_id else event_id
    connection.execute(
        "UPDATE source_listing SET canonical_event_id = %s WHERE canonical_event_id = %s",
        (winner, loser),
    )
    # Do not cascade away any user data. An unexpected dependent row fails the
    # transaction safely, preserving both canonicals for investigation/retry.
    connection.execute("DELETE FROM canonical_event WHERE id = %s", (loser,))
