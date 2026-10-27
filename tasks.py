"""Cross-platform task runner (Windows PowerShell friendly): `python tasks.py <command>`.

Run `python tasks.py help` for the list. Commands never delete Docker volumes or data.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")


def py() -> str:
    """Use the project venv's interpreter when present, else the current one."""
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def run(*cmd: str, env: dict | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **(env or {})}, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def maritime(*args: str) -> None:
    run(py(), "-m", "maritime.cli", *args, env={"PYTHONPATH": str(ROOT / "src")})


COMMANDS = {}


def task(fn):
    COMMANDS[fn.__name__.replace("_", "-")] = fn
    return fn


@task
def setup(*_: str) -> None:
    """Create .venv and install the project with dev extras."""
    if not VENV_PY.exists():
        run(sys.executable, "-m", "venv", ".venv")
    run(py(), "-m", "pip", "install", "--upgrade", "pip")
    run(py(), "-m", "pip", "install", "-e", ".[dev]")
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
        print("Created .env from .env.example; set POSTGRES_PASSWORD before starting the DB.")


@task
def db_up(*_: str) -> None:
    """Start only the PostGIS container (bound to 127.0.0.1)."""
    run("docker", "compose", "up", "-d", "--wait", "db")


@task
def db_down(*_: str) -> None:
    """Stop containers. Volumes (your data) are kept."""
    run("docker", "compose", "down")


@task
def migrate(*_: str) -> None:
    """Apply Alembic migrations."""
    run(py(), "-m", "alembic", "upgrade", "head", env={"PYTHONPATH": str(ROOT / "src")})


@task
def download(*a: str) -> None:
    """Download NOAA AIS archives for the study window (about 300 MB per day)."""
    maritime("download", *a)


@task
def ingest(*a: str) -> None:
    """Validate and load raw AIS files into PostGIS (idempotent, resumable)."""
    maritime("ingest", *a)


@task
def ports(*a: str) -> None:
    """Load NGA World Port Index ports near the study region."""
    maritime("ports", *a)


@task
def synthetic(*a: str) -> None:
    """Generate synthetic fixtures; add --load to put the demo slice in the DB."""
    maritime("synthetic", *a)


@task
def process(*a: str) -> None:
    """Trajectories, segments, port proximity and vessel-day features."""
    maritime("process", *a)


@task
def detect(*a: str) -> None:
    """Rule-based + Isolation Forest detection on real data."""
    maritime("detect", *a)


@task
def evaluate(*a: str) -> None:
    """Synthetic labelled benchmark metrics (precision/recall/F1)."""
    maritime("evaluate", *a)


@task
def network(*a: str) -> None:
    """Build the evidence-backed vessel/port/region network."""
    maritime("network", *a)


@task
def pipeline(*a: str) -> None:
    """ingest -> ports -> process -> detect -> evaluate -> network."""
    maritime("pipeline", *a)


@task
def app(*_: str) -> None:
    """Launch the Streamlit dashboard on http://127.0.0.1:8501."""
    run(
        py(),
        "-m",
        "streamlit",
        "run",
        "app/Home.py",
        "--server.address",
        "127.0.0.1",
        env={"PYTHONPATH": str(ROOT / "src")},
    )


@task
def api(*_: str) -> None:
    """Launch the FastAPI backend on http://127.0.0.1:8000 (docs at /docs)."""
    run(
        py(),
        "-m",
        "uvicorn",
        "api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        env={"PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}"},
    )


@task
def test(*a: str) -> None:
    """All tests (DB tests are skipped, not passed, if the test DB is unreachable)."""
    run(py(), "-m", "pytest", *a, env={"PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}"})


@task
def test_unit(*a: str) -> None:
    """Unit tests only (no database needed)."""
    run(
        py(),
        "-m",
        "pytest",
        "tests/unit",
        *a,
        env={"PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}"},
    )


@task
def lint(*_: str) -> None:
    """Ruff lint and format check."""
    run(py(), "-m", "ruff", "check", ".")
    run(py(), "-m", "ruff", "format", "--check", ".")


@task
def fmt(*_: str) -> None:
    """Apply Ruff formatting and safe fixes."""
    run(py(), "-m", "ruff", "format", ".")
    run(py(), "-m", "ruff", "check", "--fix", ".")


@task
def benchmark(*a: str) -> None:
    """Run scripts/benchmark.py (writes reports/benchmarks/)."""
    run(py(), "scripts/benchmark.py", *a, env={"PYTHONPATH": str(ROOT / "src")})


@task
def publish_check(*a: str) -> None:
    """Scan source files for common publication risks before a GitHub release."""
    run(py(), "scripts/publish_check.py", *a)


@task
def help(*_: str) -> None:  # noqa: A001
    """List commands."""
    for name, fn in COMMANDS.items():
        print(f"  {name:<12} {(fn.__doc__ or '').strip().splitlines()[0] if fn.__doc__ else ''}")


if __name__ == "__main__":
    name, *rest = sys.argv[1:] or ["help"]
    if name not in COMMANDS:
        print(f"Unknown command {name!r}.")
        help()
        sys.exit(2)
    COMMANDS[name](*rest)
