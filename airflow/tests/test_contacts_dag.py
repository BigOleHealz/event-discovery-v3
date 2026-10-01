import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from test_feedback import pg_url
from test_graph_dag import container_url


def test_airflow_matches_contacts_twice(database_url: str, tmp_path: Path) -> None:
    owner, recipient, contact = [uuid4() for _ in range(3)]
    with psycopg.connect(pg_url(database_url)) as connection:
        connection.execute(
            "INSERT INTO app_user (id,google_sub) VALUES (%s,%s)", (owner, str(owner))
        )
        connection.execute(
            "INSERT INTO app_user (id,google_sub,email) VALUES (%s,%s,%s)",
            (recipient, str(recipient), "dag-person@example.com"),
        )
        connection.execute(
            """
            INSERT INTO contact (id,owner_user_id,email,source)
            VALUES (%s,%s,'dag-person@example.com','manual')
        """,
            (contact, owner),
        )
    root = Path(__file__).resolve().parents[2]
    image = "event-discovery-airflow:contacts-test"
    log = tmp_path / "contacts-dag.log"
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
                f"{root / 'airflow/tests/contacts_dag_check.py'}:/tmp/contacts_dag_check.py:ro",
                "-e",
                "AIRFLOW__CORE__LOAD_EXAMPLES=False",
                "-e",
                "AIRFLOW__CORE__UNIT_TEST_MODE=True",
                "-e",
                "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=sqlite:////tmp/airflow-test.db",
                "-e",
                "CONTACT_MATCH_DAG_SCHEDULE=15 2 * * *",
                "-e",
                f"EVENT_DATABASE_URL={container_url(pg_url(database_url))}",
                image,
                "/tmp/contacts_dag_check.py",
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    try:
        assert result.returncode == 0, log.read_text()
        with psycopg.connect(pg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT matched_user_id FROM contact WHERE id=%s",
                (contact,),
            ).fetchone() == (recipient,)
            assert connection.execute("""
                SELECT status, count(*) FROM ingest.run
                WHERE airflow_dag_id='match_contacts_to_users' GROUP BY status
            """).fetchall() == [("success", 2)]
    finally:
        with psycopg.connect(pg_url(database_url)) as connection:
            connection.execute("DELETE FROM contact WHERE id=%s", (contact,))
            connection.execute("DELETE FROM app_user WHERE id=ANY(%s)", ([owner, recipient],))
            connection.execute(
                "DELETE FROM ingest.run WHERE airflow_dag_id='match_contacts_to_users'"
            )
