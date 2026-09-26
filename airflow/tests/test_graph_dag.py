from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from test_graph import (
    assert_matches_postgres,
    graph_config,  # noqa: F401 -- shared real Neo4j fixture
    pg_url,
    seed_projection,
)

from ingestion.graph import GraphConfig


def container_url(value: str) -> str:
    url = urlsplit(value)
    host = "host.docker.internal" if url.hostname in ("localhost", "127.0.0.1") else url.hostname
    auth = f"{url.username}:{url.password}@" if url.username else ""
    return urlunsplit(url._replace(netloc=f"{auth}{host}:{url.port}"))


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_airflow_rebuilds_deleted_graph(
    database_url: str, graph_config: GraphConfig, tmp_path: Path,  # noqa: F811
) -> None:
    seed_projection(database_url)
    root = Path(__file__).resolve().parents[2]
    image = "event-discovery-airflow:graph-test"
    log = tmp_path / "graph-dag.log"
    # SQLite is only Airflow's disposable metadata. Projection data uses real services.
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
            "-v", f"{root / 'airflow/tests/graph_dag_check.py'}:/tmp/graph_dag_check.py:ro",
            "-e", "AIRFLOW__CORE__LOAD_EXAMPLES=False",
            "-e", "AIRFLOW__CORE__UNIT_TEST_MODE=True",
            "-e", "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////tmp/airflow-test.db",
            "-e", f"EVENT_DATABASE_URL={container_url(pg_url(database_url))}",
            "-e", f"NEO4J_URI={container_url(graph_config.uri)}",
            "-e", f"NEO4J_USER={graph_config.user}",
            "-e", f"NEO4J_PASSWORD={graph_config.password}",
            "-e", f"NEO4J_DATABASE={graph_config.database}",
            image, "/tmp/graph_dag_check.py",
        ], stdout=output, stderr=subprocess.STDOUT, check=False)
    assert result.returncode == 0, log.read_text()
    assert_matches_postgres(database_url, graph_config)
    with psycopg.connect(pg_url(database_url)) as connection:
        assert connection.execute("""
            SELECT status, events_found, count(*) FROM ingest.run
            WHERE airflow_dag_id = 'project_to_neo4j' GROUP BY status, events_found
        """).fetchall() == [("success", 2, 3)]
