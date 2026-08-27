import os

# Docker Desktop exposes the engine through a user-path socket, but the Testcontainers
# cleanup sidecar must mount the engine's canonical in-VM socket path.
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")
