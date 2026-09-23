from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from test_dedup import CORPUS, seed_pair
from test_exact_match import psycopg_url


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_airflow_harness_resolves_and_retries(database_url: str, tmp_path: Path) -> None:
    first, second = seed_pair(database_url, CORPUS[0])
    root = Path(__file__).resolve().parents[2]
    image = "event-discovery-airflow:dedup-test"
    log = tmp_path / "dedup-dag.log"
    # Only Airflow's metadata uses disposable SQLite. Event matching still uses
    # the same real PostGIS/pgvector testcontainer as the classification suite.
    url = urlsplit(psycopg_url(database_url))
    host = "host.docker.internal" if url.hostname in ("localhost", "127.0.0.1") else url.hostname
    container_url = urlunsplit(url._replace(
        netloc=f"{url.username}:{url.password}@{host}:{url.port}",
    ))
    with log.open("w") as output:
        build = subprocess.run(
            ["docker", "build", "-t", image, str(root / "airflow")],
            env={**os.environ, "BUILDX_CONFIG": str(tmp_path / "buildx")},
            stdout=output, stderr=subprocess.STDOUT, check=False,
        )
        assert build.returncode == 0, log.read_text()
        result = subprocess.run([
            "docker", "run", "--rm", "--add-host", "host.docker.internal:host-gateway",
            "--entrypoint", "python",
            "-v", f"{root / 'airflow/tests/dedup_dag_check.py'}:/tmp/dedup_dag_check.py:ro",
            "-e", "AIRFLOW__CORE__LOAD_EXAMPLES=False",
            "-e", "AIRFLOW__CORE__UNIT_TEST_MODE=True",
            "-e", "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////tmp/airflow-test.db",
            "-e", f"EVENT_DATABASE_URL={container_url}",
            "-e", "EMBEDDING_PROVIDER=openai",
            "-e", "EMBEDDING_MODEL=text-embedding-3-small",
            "-e", "EMBEDDING_API_BASE_URL=http://127.0.0.1:1",
            "-e", "EMBEDDING_API_KEY=fixture-only",
            image, "/tmp/dedup_dag_check.py",
        ], stdout=output, stderr=subprocess.STDOUT, check=False)
    assert result.returncode == 0, log.read_text()
    with psycopg.connect(psycopg_url(database_url)) as connection:
        assert connection.execute("SELECT id FROM canonical_event").fetchall() == [(first,)]
        assert connection.execute(
            "SELECT canonical_event_id, dedup_state FROM source_listing WHERE id = %s",
            (second,),
        ).fetchone() == (first, "same")
        assert connection.execute(
            "SELECT sum(events_deduped) FROM ingest.run"
        ).fetchone() == (1,)
