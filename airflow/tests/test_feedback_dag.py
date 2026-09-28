import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from test_feedback import feedback_db, pg_url, seed_attendee  # noqa: F401
from test_graph_dag import container_url


def test_airflow_requests_feedback_once(feedback_db: str, tmp_path: Path) -> None:  # noqa: F811
    with psycopg.connect(pg_url(feedback_db)) as connection:
        seed_attendee(
            connection,
            0,
            starts=datetime(2000, 1, 1, tzinfo=UTC),
            ends=datetime(2000, 1, 2, tzinfo=UTC),
        )
    root = Path(__file__).resolve().parents[2]
    image = "event-discovery-airflow:feedback-test"
    log = tmp_path / "feedback-dag.log"
    with log.open("w") as output:
        build = subprocess.run(
            ["docker", "build", "-t", image, str(root / "airflow")],
            env={**os.environ, "BUILDX_CONFIG": str(tmp_path / "buildx")},
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert build.returncode == 0, log.read_text()
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
                f"{root / 'airflow/tests/feedback_dag_check.py'}:/tmp/feedback_dag_check.py:ro",
                "-e",
                "AIRFLOW__CORE__LOAD_EXAMPLES=False",
                "-e",
                "AIRFLOW__CORE__UNIT_TEST_MODE=True",
                "-e",
                "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////tmp/airflow-test.db",
                "-e",
                "EVENT_FEEDBACK_DAG_SCHEDULE=15 10 * * *",
                "-e",
                f"EVENT_DATABASE_URL={container_url(pg_url(feedback_db))}",
                image,
                "/tmp/feedback_dag_check.py",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    assert result.returncode == 0, log.read_text()
    with psycopg.connect(pg_url(feedback_db)) as connection:
        assert connection.execute("SELECT state FROM attendance").fetchall() == [("attended",)]
        assert connection.execute("SELECT count(*) FROM event_feedback_request").fetchone() == (1,)
        assert connection.execute("""
            SELECT status, count(*) FROM ingest.run WHERE airflow_dag_id='request_event_feedback'
            GROUP BY status
        """).fetchall() == [("success", 2)]
