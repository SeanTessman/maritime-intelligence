"""Offline pre-publication checks for files included in a source release."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 25 * 1024 * 1024
BLOCKED_NAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
BLOCKED_SUFFIXES = (".pem", ".p12", ".pfx", ".key")
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[:=]\s*[\"\']([^\"\']{8,})[\"\']"
    ),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
]
IGNORED_VALUES = {
    "",
    "changeme",
    "change_me",
    "placeholder",
    "example",
    "your_key",
    "none",
    "null",
    "***",
    "ci_only_password",
}


def release_files(root: Path) -> list[Path]:
    """Use tracked files when Git is present; otherwise scan a source archive."""
    result = (
        subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False)
        if __import__("shutil").which("git")
        else None
    )
    if result and result.returncode == 0 and result.stdout:
        return [
            root / p.decode("utf-8", "surrogateescape") for p in result.stdout.split(b"\0") if p
        ]
    excluded = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
    return [
        p
        for p in root.rglob("*")
        if p.is_file() and not any(x in excluded for x in p.relative_to(root).parts)
    ]


def check(root: Path) -> list[str]:
    issues = []
    for path in release_files(root):
        if not path.exists():
            continue
        rel = path.relative_to(root).as_posix()
        if path.name in BLOCKED_NAMES or path.suffix.lower() in BLOCKED_SUFFIXES:
            issues.append(f"{rel}: private credential/configuration file")
        if path.stat().st_size > MAX_FILE_BYTES:
            issues.append(f"{rel}: exceeds 25 MiB source-release limit")
        if path.suffix.lower() not in {
            ".py",
            ".toml",
            ".yml",
            ".yaml",
            ".json",
            ".md",
            ".txt",
            ".sh",
            ".ps1",
            ".ini",
            ".cfg",
        } and path.name not in {".env.example", "Dockerfile"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeError, OSError):
            continue
        for n, line in enumerate(lines, 1):
            if path.name == ".env.example" or line.lstrip().startswith(("#", "//")):
                continue
            for pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if match and (
                    not match.lastindex or match.group(1).strip("\"'").lower() not in IGNORED_VALUES
                ):
                    issues.append(f"{rel}:{n}: possible secret (inspect manually)")
                    break
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    issues = check(root)
    if issues:
        print(f"Publication check: {len(issues)} issue(s) found:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("Publication check passed: no obvious credentials or oversized files detected.")
    print("Note: this is a heuristic check, not a guarantee that all content is safe to publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
