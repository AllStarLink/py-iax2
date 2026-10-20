# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""CALLTOKEN anti-spoofing tokens (Asterisk 1.6.2+ / RFC 5456 errata).

Before allocating call state for a NEW or REGREQ, a server may require the
client to prove it can receive packets at its source address:

1. The client sends NEW/REGREQ with an **empty** CALLTOKEN IE.
2. The server answers with a CALLTOKEN command carrying a token.
3. The client resends the original frame with the token in the CALLTOKEN IE.
4. The server validates the token and proceeds normally.

Tokens are stateless on the server: ``"<unix time>?<sha1 hex>"`` where the
hash covers the client's address, the time and a per-process random secret
(Asterisk's ``CALLTOKEN_HASH_FORMAT``).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

__all__ = ["CallTokenGenerator", "format_addr"]


def format_addr(host: str, port: int) -> str:
    """Format an address like Asterisk's ``ast_sockaddr_stringify()``.

    IPv4 is ``host:port``; IPv6 is ``[host]:port``.
    """
    if ":" in host:
        return f"[{host}]:{port}"
    return f"{host}:{port}"


class CallTokenGenerator:
    """Issues and validates call tokens.

    Args:
        max_delay: Seconds a token stays valid (Asterisk ``maxcalltokendelay``,
            default 10).
        secret: Random integer mixed into the hash; generated if omitted.
    """

    def __init__(self, max_delay: int = 10, secret: int | None = None):
        self.max_delay = max_delay
        self._secret = secret if secret is not None else secrets.randbelow(2**31)

    def _hash(self, host: str, port: int, when: int) -> str:
        data = f"{format_addr(host, port)}{when}{self._secret}"
        return hashlib.sha1(data.encode(), usedforsecurity=False).hexdigest()

    def issue(self, host: str, port: int, now: float | None = None) -> str:
        """Return a new token for a client at ``(host, port)``."""
        t = int(time.time() if now is None else now)
        return f"{t}?{self._hash(host, port, t)}"

    def validate(self, token: str | bytes, host: str, port: int, now: float | None = None) -> bool:
        """Return ``True`` if ``token`` was issued to ``(host, port)`` and has not expired."""
        if isinstance(token, bytes):
            token = token.decode("ascii", "replace")
        ts_text, sep, rec_hash = token.partition("?")
        if not sep or not ts_text.isdigit():
            return False
        rec_time = int(ts_text)
        if not hmac.compare_digest(self._hash(host, port, rec_time), rec_hash):
            return False
        t = int(time.time() if now is None else now)
        return rec_time <= t and t - rec_time < self.max_delay
