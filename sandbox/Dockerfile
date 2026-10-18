# Minimal, disposable image used to run agent-generated code/tests.
# Containers built from this image are always started with:
#   - network_disabled=True   (no internet/host access)
#   - a mem_limit and cpu_quota (see app/config.py)
#   - force-removed immediately after the run (see app/sandbox/docker_executor.py)
#
# Build:
#   docker build -t mio-sandbox-python:latest sandbox/
FROM python:3.11-slim

RUN pip install --no-cache-dir pytest

# Non-root user: even though the container is network-disabled and ephemeral,
# generated code should never run as root inside it.
RUN useradd --create-home --uid 1001 sandboxuser
USER sandboxuser

WORKDIR /workspace

# No CMD/ENTRYPOINT — the executor always supplies an explicit command.
