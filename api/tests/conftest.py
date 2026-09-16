import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

# Docker Desktop exposes the engine through a user-path socket, but the Testcontainers
# cleanup sidecar must mount the engine's canonical in-VM socket path.
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

import pytest
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    image = "event-discovery-postgres:16-3.4-vector-0.8.6"
    subprocess.run(
        ["docker", "build", "-t", image, str(Path(__file__).resolve().parents[2] / "postgres")],
        check=True,
    )
    with PostgresContainer(image, driver="psycopg") as postgres:
        yield postgres.get_connection_url()
