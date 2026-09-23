-- Isolated Compose test data, three ambiguous pairs with durable original evidence.
DELETE FROM dedup_review
WHERE listing_a_id IN (
    SELECT md5('phase4f-' || n || '-' || source)::uuid
    FROM generate_series(1, 3) n CROSS JOIN (VALUES ('eventbrite'), ('meetup')) sources(source)
);
-- The reviewer is provisioned by the API entrypoint, not by this fixture.

INSERT INTO venue (id, name, formatted_address, google_place_id, location)
VALUES ('4f000000-0000-0000-0000-000000000002', 'Review Jazz Room',
    '123 Test Street, Philadelphia', 'review-fixture-place',
    ST_SetSRID(ST_MakePoint(-75.18, 39.96), 4326)) ON CONFLICT (id) DO NOTHING;

INSERT INTO canonical_event (
    id, title, description, starts_at, timezone, venue_id, location, primary_category
)
SELECT md5('phase4f-' || n || '-' || source)::uuid,
    'Review ' || source || ' ' || n, 'Original ' || source || ' description ' || n,
    '2026-10-06T23:00:00Z'::timestamptz + n * interval '1 day', 'America/New_York',
    '4f000000-0000-0000-0000-000000000002',
    ST_SetSRID(ST_MakePoint(-75.18, 39.96), 4326), 'review-e2e'
FROM generate_series(1, 3) n CROSS JOIN (VALUES ('eventbrite'), ('meetup')) sources(source)
ON CONFLICT (id) DO NOTHING;

INSERT INTO source_listing (
    id, canonical_event_id, source, source_event_id, url, raw_payload, ingestion_run_id,
    dedup_state
)
SELECT md5('phase4f-' || n || '-' || source)::uuid,
    md5('phase4f-' || n || '-' || source)::uuid, source, 'phase4f-' || n,
    'https://' || source || '.test/review/' || n, '{}',
    '4f000000-0000-0000-0000-000000000003', 'distinct'
FROM generate_series(1, 3) n CROSS JOIN (VALUES ('eventbrite'), ('meetup')) sources(source)
ON CONFLICT (id) DO UPDATE SET canonical_event_id = EXCLUDED.canonical_event_id,
    dedup_state = EXCLUDED.dedup_state;

UPDATE source_listing SET dedup_state = 'review',
    dedup_match_id = md5('phase4f-' || n || '-eventbrite')::uuid,
    dedup_similarity = 0.8,
    dedup_checked_at = '2026-09-22T12:00:00Z'::timestamptz + n * interval '1 minute'
FROM generate_series(1, 3) n
WHERE id = md5('phase4f-' || n || '-meetup')::uuid;
