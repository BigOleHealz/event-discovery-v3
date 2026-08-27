import os
from collections.abc import Iterator

# Docker Desktop exposes the engine through a user-path socket, but the Testcontainers
# cleanup sidecar must mount the engine's canonical in-VM socket path.
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

import pytest
from testcontainers.community.postgres import PostgresContainer


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgis/postgis:16-3.4", driver="psycopg") as postgres:
        yield postgres.get_connection_url()
