-- A resolved occurrence with multiple listings, including two from one provider.
-- Fixed dates are queried with an explicit range by the browser test.
INSERT INTO canonical_event (
    id, title, description, starts_at, timezone, location, primary_category
) VALUES (
    '4e000000-0000-0000-0000-000000000001', 'One jazz show, two sources',
    'An evening of jazz in Philadelphia.', '2026-09-04T23:00:00Z', 'America/New_York',
    ST_SetSRID(ST_MakePoint(-75.18, 39.96), 4326)::geography, 'dedup-e2e'
) ON CONFLICT (id) DO NOTHING;

INSERT INTO source_listing (
    id, canonical_event_id, source, source_event_id, url, registration_url,
    raw_payload, ingestion_run_id, last_seen_at, dedup_state
) VALUES
    ('4e000000-0000-0000-0000-000000000002', '4e000000-0000-0000-0000-000000000001',
     'eventbrite', 'dedup-e2e-primary', 'https://www.eventbrite.com/e/jazz-show',
     'https://www.eventbrite.com/e/jazz-show-tickets', '{}',
     '4e000000-0000-0000-0000-000000000005', '2026-09-01T00:00:00Z', 'distinct'),
    ('4e000000-0000-0000-0000-000000000003', '4e000000-0000-0000-0000-000000000001',
     'meetup', 'dedup-e2e-meetup', 'https://www.meetup.com/jazz/events/123/', NULL, '{}',
     '4e000000-0000-0000-0000-000000000005', '2026-09-01T00:00:00Z', 'same'),
    ('4e000000-0000-0000-0000-000000000004', '4e000000-0000-0000-0000-000000000001',
     'eventbrite', 'dedup-e2e-secondary', 'https://www.eventbrite.com/e/older-jazz-show', NULL,
     '{}', '4e000000-0000-0000-0000-000000000005', '2026-08-31T00:00:00Z', 'same')
ON CONFLICT (id) DO NOTHING;
