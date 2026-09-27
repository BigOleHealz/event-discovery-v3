-- Isolated Compose E2E database only. Fixed future dates keep tests deterministic.
INSERT INTO canonical_event (id, title, starts_at, timezone, primary_category, location)
VALUES
    ('5b000000-0000-0000-0000-000000000001', '5b Bebop Evening',
     '2050-09-27T18:00:00Z', 'America/New_York', 'Bebop',
     ST_SetSRID(ST_MakePoint(-75.16, 39.95), 4326)::geography),
    ('5b000000-0000-0000-0000-000000000002', '5b Rock Evening',
     '2050-09-27T18:00:00Z', 'America/New_York', 'Rock',
     ST_SetSRID(ST_MakePoint(-75.17, 39.95), 4326)::geography),
    ('5b000000-0000-0000-0000-000000000003', '5b Science Evening',
     '2050-09-27T18:00:00Z', 'America/New_York', 'Science & Technology',
     ST_SetSRID(ST_MakePoint(-75.18, 39.95), 4326)::geography);
