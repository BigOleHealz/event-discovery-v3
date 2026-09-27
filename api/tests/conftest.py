import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

# Docker Desktop exposes the engine through a user-path socket, but the Testcontainers
# cleanup sidecar must mount the engine's canonical in-VM socket path.
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

import pytest
from ingestion.graph import GraphConfig
from testcontainers.community.neo4j import Neo4jContainer
from testcontainers.community.postgres import PostgresContainer

from app.graph import graph_driver


@pytest.fixture(scope="session")
def graph_environment() -> Iterator[GraphConfig]:
    container = Neo4jContainer("neo4j:5.26-community", username="neo4j", password="category-test")
    with container as graph:
        with pytest.MonkeyPatch.context() as patch:
            config = GraphConfig(graph.get_connection_url(), "neo4j", "category-test")
            for key, value in {
                "NEO4J_URI": config.uri,
                "NEO4J_USER": config.user,
                "NEO4J_PASSWORD": config.password,
                "NEO4J_DATABASE": config.database,
            }.items():
                patch.setenv(key, value)
            yield config
            if graph_driver.cache_info().currsize:
                graph_driver().close()
                graph_driver.cache_clear()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    image = "event-discovery-postgres:16-3.4-vector-0.8.6"
    subprocess.run(
        ["docker", "build", "-t", image, str(Path(__file__).resolve().parents[2] / "postgres")],
        check=True,
    )
    with PostgresContainer(image, driver="psycopg") as postgres:
        yield postgres.get_connection_url()
