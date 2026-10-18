from __future__ import annotations
import io
import tarfile
import time
from dataclasses import dataclass

try:
    import docker
    from docker.errors import DockerException
except ImportError:  # docker sdk not installed in this environment
    docker = None
    DockerException = Exception

from app.config import settings


class SandboxUnavailable(RuntimeError):
    pass


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    timed_out: bool = False


def _client():
    if docker is None:
        raise SandboxUnavailable("docker Python SDK is not installed.")
    try:
        return docker.from_env()
    except DockerException as e:
        raise SandboxUnavailable(f"Docker daemon not reachable: {e}") from e


def _build_tar(files: dict[str, str]) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            info.mtime = time.time()
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def run_python_tests(files: dict[str, str], entrypoint: str = "python -m pytest -q") -> SandboxResult:

    client = _client()
    container = client.containers.create(
        image=settings.docker_sandbox_image,
        command=["sh", "-c", f"{entrypoint} 2>&1"],
        working_dir="/workspace",
        network_disabled=True,
        mem_limit=settings.sandbox_mem_limit,
        cpu_quota=settings.sandbox_cpu_quota,
        detach=True,
    )
    try:
        container.put_archive("/workspace", _build_tar(files))
        container.start()
        try:
            result = container.wait(timeout=settings.sandbox_timeout_seconds)
            exit_code = result.get("StatusCode", 1)
            timed_out = False
        except Exception:
            container.kill()
            exit_code, timed_out = 124, True
        logs = container.logs().decode("utf-8", errors="replace")
        return SandboxResult(exit_code=exit_code, stdout=logs, timed_out=timed_out)
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass


