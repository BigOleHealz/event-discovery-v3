-- Use the category fixtures so the browser can follow a suggestion outside its Jazz filter.
INSERT INTO source_listing (id, canonical_event_id, source, source_event_id, url,
                            raw_payload, ingestion_run_id, dedup_state)
SELECT ('5c000000-0000-0000-0000-00000000000' || n)::uuid,
       ('5b000000-0000-0000-0000-00000000000' || n)::uuid,
       'fixture', '5c-' || n, 'https://fixture.test/register/' || n, '{}',
       '5c000000-0000-0000-0000-000000000099', 'distinct'
FROM generate_series(1, 3) AS n;
UPDATE source_listing SET dedup_match_id = '5c000000-0000-0000-0000-000000000002',
    dedup_similarity = 0.72 WHERE id = '5c000000-0000-0000-0000-000000000001';
UPDATE source_listing SET dedup_match_id = '5c000000-0000-0000-0000-000000000001',
    dedup_similarity = 0.66 WHERE id = '5c000000-0000-0000-0000-000000000002';
UPDATE source_listing SET dedup_match_id = '5c000000-0000-0000-0000-000000000001',
    dedup_similarity = -0.1 WHERE id = '5c000000-0000-0000-0000-000000000003';
