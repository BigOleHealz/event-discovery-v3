from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_graph_dag import container_url
from test_sites import FIXTURES, adapter, html, seed

from ingestion.site_repository import SiteRepository


@pytest.mark.usefixtures("clean_ingestion_tables")
def test_real_mapped_site_dags_replay_without_model_calls(
    database_url: str, tmp_path: Path
) -> None:
    calls = {"model": 0, "browser": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/responses":
                calls["model"] += 1
                result = json.loads((FIXTURES / "site/plan-response.json").read_text())
            elif self.path == "/v1/fetch":
                calls["browser"] += 1
                page = (
                    (FIXTURES / "site/empty.html").read_text()
                    if "page=2" in body["url"]
                    else html()
                )
                result = {"url": body["url"], "html": page, "http_status": 200}
            else:
                raise AssertionError("unexpected external request")
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[2]
    image = "event-discovery-airflow:site-test"
    base = f"http://host.docker.internal:{server.server_port}"
    seed(database_url, adapter("stagehand"))
    # No public site is reachable through the deterministic browser substitute.
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        connection.execute(
            "UPDATE ingest.crawl_target SET source_location=%s WHERE source='fixture-site'",
            (Jsonb({"kind": "listing_url", "url": base + "/events"}),),
        )
    log = tmp_path / "site-dag.log"
    try:
        with log.open("w") as output:
            built = subprocess.run(
                ["docker", "build", "-t", image, str(root / "airflow")],
                env={**os.environ, "BUILDX_CONFIG": str(tmp_path / "buildx")},
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
            assert built.returncode == 0, log.read_text()
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--add-host",
                    "host.docker.internal:host-gateway",
                    "--entrypoint",
                    "python",
                    "-v",
                    f"{root / 'airflow/tests/site_dag_check.py'}:/tmp/site_dag_check.py:ro",
                    "-e",
                    "AIRFLOW__CORE__LOAD_EXAMPLES=False",
                    "-e",
                    "AIRFLOW__CORE__UNIT_TEST_MODE=True",
                    "-e",
                    "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////tmp/site-airflow.db",
                    "-e",
                    f"EVENT_DATABASE_URL={container_url(SiteRepository(database_url).url)}",
                    "-e",
                    f"STAGEHAND_BASE_URL={base}",
                    "-e",
                    "STAGEHAND_API_TOKEN=test-only",
                    "-e",
                    f"EXTRACTION_API_URL={base}/responses",
                    "-e",
                    "EXTRACTION_API_KEY=test-only",
                    image,
                    "/tmp/site_dag_check.py",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                check=False,
            )
        assert result.returncode == 0, log.read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert calls == {"model": 1, "browser": 4}
    with psycopg.connect(SiteRepository(database_url).url) as connection:
        assert connection.execute("SELECT count(*) FROM source_listing").fetchone() == (1,)
        assert connection.execute("""
            SELECT status, events_found, count(*) FROM ingest.run
            WHERE source='fixture-site' GROUP BY status, events_found
        """).fetchall() == [("success", 1, 2)]
