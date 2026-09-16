# Dedup classification corpus

Hand-labelled event scenarios: cross-source duplicates, an ambiguous pair, unrelated
co-located events, recurring Tuesdays, and identically named events in different cities.
Each row is a concrete occurrence. Labels are independent of numeric score assertions.

The embedding prefixes are hand-written external-service fixtures, zero-padded to 1536
components. They exercise pgvector and resolution deterministically without API calls;
they are not captured model outputs and do not measure model precision or recall.
Future recorded embeddings and admin decisions can extend this corpus.
