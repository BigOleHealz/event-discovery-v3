# Airflow

Airflow 3 runs through separate API-server, scheduler, and DAG-processor Compose services
using `LocalExecutor`. Its internal metadata lives in the dedicated `AIRFLOW_DB` database;
ingestion observability remains in the application database's `ingest` schema.
