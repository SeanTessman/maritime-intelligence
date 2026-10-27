"""Download NOAA Marine Cadastre daily AIS archives, one day at a time.

* Verifies each URL with a HEAD request before downloading (no invented URLs).
* Resumes partial downloads with HTTP Range requests.
* Writes a `<file>.sha256` sidecar so later runs can skip verified files.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from pathlib import Path

import requests

from maritime.config import get_settings

logger = logging.getLogger(__name__)


def ais_filename(day: date) -> str:
    return f"AIS_{day:%Y_%m_%d}.zip"


def ais_url(day: date, base_url: str | None = None) -> str:
    base = (base_url or get_settings().miadp_ais_base_url).rstrip("/")
    return f"{base}/{day:%Y}/{ais_filename(day)}"


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end date precedes start date")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def head_check(url: str, timeout: float = 30) -> int | None:
    """Return Content-Length if the URL exists, else None."""
    resp = requests.head(url, timeout=timeout, allow_redirects=True)
    if resp.status_code != 200:
        logger.warning("HEAD %s -> HTTP %s", url, resp.status_code)
        return None
    return int(resp.headers.get("Content-Length", 0)) or None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def download_day(day: date, dest_dir: Path | None = None, timeout: float = 60) -> Path:
    """Download one day's archive (resumable). Returns the local path."""
    dest_dir = dest_dir or get_settings().raw_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = ais_url(day)
    target = dest_dir / ais_filename(day)
    sidecar = target.with_suffix(".zip.sha256")
    expected = head_check(url)
    if expected is None:
        raise FileNotFoundError(f"NOAA archive not available: {url}")
    if target.exists() and target.stat().st_size == expected and sidecar.exists():
        logger.info("%s already downloaded and verified", target.name)
        return target
    part = target.with_suffix(".zip.part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, stream=True, headers=headers, timeout=timeout) as resp:
        resp.raise_for_status()
        mode = "ab" if have and resp.status_code == 206 else "wb"
        with part.open(mode) as f:
            for block in resp.iter_content(chunk_size=4 << 20):
                f.write(block)
    if part.stat().st_size != expected:
        raise OSError(f"Size mismatch for {target.name}: {part.stat().st_size} != {expected}")
    part.replace(target)
    sidecar.write_text(f"{_sha256(target)}  {target.name}\n")
    logger.info("Downloaded %s (%d bytes)", target.name, expected)
    return target


def download_range(start: date, end: date, dest_dir: Path | None = None) -> list[Path]:
    return [download_day(d, dest_dir) for d in date_range(start, end)]
