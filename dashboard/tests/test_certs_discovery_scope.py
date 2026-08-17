"""
Certificate SANs must come from configuration, never from an application's data.

`certs/generate-certs.sh` discovers `*.lh` hostnames by grepping `hosting/app-available/`. That
directory also holds seeded application data — 3.1 GB of MongoDB BSON in one real case here — and
the recursive grep read all of it. Two defects in one:

  * every hostname-shaped byte sequence in a binary log became a SAN on the certificate this
    machine *trusts*, which is a supply route from captured HTTP traffic into the local trust
    store; and
  * `--list` took ~100 s against a 120 s test timeout, so the suite failed whenever the machine
    was busy.

These tests pin the fix by planting a hostname where a real one would never be declared.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "certs" / "generate-certs.sh"
APPS = ROOT / "hosting" / "app-available"


def _list_hosts() -> str:
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--list"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture
def planted_app():
    """A throwaway app slot with a hostname in `data/` and another in its manifest."""
    if not APPS.is_dir():
        pytest.skip("hosting/app-available not present in this checkout")
    slug = f"zz-certscope-{uuid.uuid4().hex[:8]}"
    app = APPS / slug
    (app / "data" / "mongo").mkdir(parents=True)
    (app / "node_modules" / "pkg").mkdir(parents=True)
    try:
        # Stand-in for a captured HTTP log inside a database dump.
        (app / "data" / "mongo" / "dump.bson").write_text("garbage http://planted-from-data.lh more")
        (app / "node_modules" / "pkg" / "readme.md").write_text("see http://planted-from-nodemodules.lh")
        # A legitimately declared URL, which must still be found.
        (app / "leco.yaml").write_text(
            "urls:\n  - role: frontend\n    publicUrl: https://planted-from-manifest.lh/\n"
        )
        yield
    finally:
        shutil.rmtree(app, ignore_errors=True)


def test_data_directories_never_contribute_certificate_names(planted_app):
    out = _list_hosts()

    assert "planted-from-manifest.lh" in out, "a declared URL must still reach the certificate"
    assert "planted-from-data.lh" not in out, (
        "a hostname read out of an application's data directory became a certificate SAN — "
        "binary dumps can contain arbitrary captured traffic"
    )
    assert "planted-from-nodemodules.lh" not in out


def test_discovery_is_fast_enough_not_to_be_flaky(planted_app):
    """The old implementation took ~100 s against this suite's 120 s timeout."""
    start = time.monotonic()
    _list_hosts()
    elapsed = time.monotonic() - start

    assert elapsed < 30, (
        f"--list took {elapsed:.1f}s; it is scanning something it should be pruning, and the "
        "120s-timeout tests around it will start failing on a busy machine"
    )


def test_prune_list_covers_the_directories_that_actually_hurt():
    """Guard the list itself — dropping `data` is what caused the original defect."""
    text = SCRIPT.read_text()
    assert "PRUNE_DIRS=(" in text
    prune_line = next(line for line in text.splitlines() if line.startswith("PRUNE_DIRS=("))
    for expected in ("data", "node_modules", ".git"):
        assert expected in prune_line, f"{expected} must stay pruned"
