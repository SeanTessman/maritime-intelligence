"""Publication check regression tests."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "publish_check", Path(__file__).resolve().parents[2] / "scripts" / "publish_check.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_flags_private_key_and_large_file(tmp_path):
    (tmp_path / "id_rsa").write_text("private")
    (tmp_path / "big.bin").write_bytes(b"x" * (25 * 1024 * 1024 + 1))
    issues = module.check(tmp_path)
    assert any("private credential" in issue for issue in issues)
    assert any("25 MiB" in issue for issue in issues)


def test_detects_literal_secret(tmp_path):
    secret_line = (
        'api_key = "' + "super_secret_12345" + '"\n'
    )  # built at runtime so this file itself is not flagged
    (tmp_path / "config.py").write_text(secret_line)
    assert any("possible secret" in issue for issue in module.check(tmp_path))


def test_example_config_is_allowed(tmp_path):
    (tmp_path / ".env.example").write_text("PASSWORD=placeholder\n")
    assert module.check(tmp_path) == []
