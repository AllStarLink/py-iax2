"""Fixtures for interoperability tests against a local Asterisk.

Run with ``pytest -m interop`` after ``sudo python3 tests/interop/setup_asterisk.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import iax2

HERE = Path(__file__).resolve().parent
ASTERISK = ("127.0.0.1", 4569)


def ast(cmd: str) -> str:
    """Run an Asterisk CLI command (needs passwordless sudo)."""
    r = subprocess.run(["sudo", "-n", "asterisk", "-rx", cmd], capture_output=True, text=True,
                       timeout=10)
    return r.stdout


def hangup_test_channels() -> None:
    """Hang up only the IAX2 channels created by these tests (never the node's own)."""
    for line in ast("core show channels concise").splitlines():
        name = line.split("!", 1)[0]
        if name.startswith("IAX2/pyiax-"):
            ast(f"channel request hangup {name}")


def _asterisk_ready() -> str | None:
    try:
        out = ast("iax2 show users")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cannot talk to Asterisk: {exc}"
    if "pyiax-md5" not in out:
        return "Asterisk not configured; run: sudo python3 tests/interop/setup_asterisk.py"
    return None


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "interop" in str(item.fspath):
            item.add_marker(pytest.mark.interop)


@pytest.fixture(scope="session")
def asterisk():
    reason = _asterisk_ready()
    if reason:
        pytest.skip(reason)
    return ast


@pytest.fixture
async def ep(asterisk):
    e = iax2.Endpoint("127.0.0.1", 0)
    await e.start()
    yield e
    await e.close()


@pytest.fixture
def rsa_key():
    from cryptography.hazmat.primitives import serialization

    path = HERE / "pyiax-test.key"
    if not path.exists():
        pytest.skip("RSA test key missing; rerun setup_asterisk.py")
    return serialization.load_pem_private_key(path.read_bytes(), password=None)
