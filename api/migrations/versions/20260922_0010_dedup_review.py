"""Phase 4f: durable, labelled review pairs, including existing 4d outcomes."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260922_0010"
down_revision: str | None = "20260916_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE dedup_review (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            listing_a_id UUID NOT NULL REFERENCES source_listing(id),
            listing_b_id UUID NOT NULL REFERENCES source_listing(id),
            similarity_score NUMERIC NOT NULL CHECK (similarity_score BETWEEN -1 AND 1),
            time_delta_minutes INT NOT NULL CHECK (time_delta_minutes >= 0),
            distance_meters INT NOT NULL CHECK (distance_meters >= 0),
            snapshot_a JSONB NOT NULL,
            snapshot_b JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'merged', 'distinct', 'skipped')),
            decided_by UUID REFERENCES app_user(id),
            decided_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (listing_a_id, listing_b_id),
            CHECK (listing_a_id < listing_b_id),
            CHECK ((status = 'pending' AND decided_by IS NULL AND decided_at IS NULL)
                OR (status <> 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL))
        );
        CREATE INDEX ix_dedup_review_queue ON dedup_review (created_at, id)
            WHERE status = 'pending';

        -- Capture the original evidence before later merges change canonical fields.
        -- Sorted listing IDs collapse A/B and B/A; retries never overwrite a label.
        CREATE FUNCTION enqueue_dedup_review(incoming_id UUID) RETURNS VOID
        LANGUAGE sql AS $$
            INSERT INTO dedup_review (
                listing_a_id, listing_b_id, similarity_score, time_delta_minutes,
                distance_meters, snapshot_a, snapshot_b, created_at
            )
            SELECT a.id, b.id, incoming.dedup_similarity,
                round(abs(extract(epoch FROM (ea.starts_at - eb.starts_at))) / 60)::int,
                round(ST_Distance(ea.location, eb.location))::int,
                jsonb_build_object('listing_id', a.id, 'title', ea.title,
                    'description', ea.description, 'starts_at', ea.starts_at,
                    'ends_at', ea.ends_at, 'timezone', ea.timezone,
                    'venue', va.name, 'address', va.formatted_address,
                    'source', a.source, 'url', a.url),
                jsonb_build_object('listing_id', b.id, 'title', eb.title,
                    'description', eb.description, 'starts_at', eb.starts_at,
                    'ends_at', eb.ends_at, 'timezone', eb.timezone,
                    'venue', vb.name, 'address', vb.formatted_address,
                    'source', b.source, 'url', b.url),
                incoming.dedup_checked_at
            FROM source_listing incoming
            JOIN source_listing a ON a.id = least(incoming.id, incoming.dedup_match_id)
            JOIN source_listing b ON b.id = greatest(incoming.id, incoming.dedup_match_id)
            JOIN canonical_event ea ON ea.id = a.canonical_event_id
            JOIN canonical_event eb ON eb.id = b.canonical_event_id
            LEFT JOIN venue va ON va.id = ea.venue_id
            LEFT JOIN venue vb ON vb.id = eb.venue_id
            WHERE incoming.id = incoming_id AND incoming.dedup_state = 'review'
              AND incoming.dedup_match_id IS NOT NULL AND ea.id <> eb.id
            ON CONFLICT (listing_a_id, listing_b_id) DO NOTHING;
        $$;
        CREATE FUNCTION queue_dedup_review() RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM enqueue_dedup_review(NEW.id);
            RETURN NEW;
        END $$;
        CREATE TRIGGER queue_dedup_review AFTER INSERT OR UPDATE OF
            dedup_state, dedup_match_id, dedup_similarity, dedup_checked_at
            ON source_listing FOR EACH ROW WHEN (NEW.dedup_state = 'review')
            EXECUTE FUNCTION queue_dedup_review();
        SELECT enqueue_dedup_review(id) FROM source_listing WHERE dedup_state = 'review';
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER queue_dedup_review ON source_listing;
        DROP FUNCTION queue_dedup_review();
        DROP FUNCTION enqueue_dedup_review(UUID);
        DROP TABLE dedup_review;
    """)
