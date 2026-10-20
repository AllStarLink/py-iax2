# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""AES-128 call encryption (placeholder; implemented in a later milestone)."""

from __future__ import annotations

from typing import Any

__all__ = ["available", "enable", "rotate_key"]


def available() -> bool:
    """``True`` if call encryption can be used."""
    return False


def enable(session: Any, challenge: str, secret: str) -> None:
    """Enable encryption on ``session``."""


def rotate_key(session: Any, ies: Any) -> None:
    """Handle an RTKEY message."""
