# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Jason D. McCormick
"""IAX2 authentication: plaintext, MD5 challenge/response and RSA signatures.

The behaviour mirrors Asterisk 22:

* **MD5** — the response is the lowercase hex MD5 of ``challenge + secret``.
  The verifier compares case-insensitively and accepts any of several
  secrets separated by ``;``.
* **RSA** — the response is the base64 PKCS#1 v1.5 / SHA-1 signature of the
  challenge text, made with the caller's private key. Asterisk only handles
  1024-bit keys (128-byte signatures). Requires the optional
  ``cryptography`` package (``pip install iax2[crypto]``).
* **Plaintext** — the secret is sent in the PASSWORD IE (discouraged).

The client picks a method the way Asterisk's ``authenticate()`` does: RSA if
a private key is configured and the server offers RSA, else MD5, else
plaintext.

Example::

    >>> from iax2.auth import Credentials, build_auth_reply, md5_response
    >>> from iax2.constants import AuthMethod
    >>> md5_response("123456", "s3cret")
    '5ed1f0dc9003f2a40604fea3cf6cd609'
    >>> ies, method = build_auth_reply(Credentials(secret="s3cret"), AuthMethod.MD5, "123456")
    >>> method
    <AuthMethod.MD5: 2>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .constants import IE, AuthMethod
from .exceptions import AuthenticationError
from .ies import IEs

if TYPE_CHECKING:  # pragma: no cover
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

__all__ = [
    "Credentials",
    "RSAKeyStore",
    "build_auth_reply",
    "md5_digest",
    "md5_response",
    "new_challenge",
    "rsa_sign",
    "rsa_verify",
    "verify_auth_reply",
]


def new_challenge() -> str:
    """Return a fresh challenge string.

    Asterisk prints a random 31-bit integer into a 10-byte buffer, so the
    challenge is at most 9 decimal digits; we generate the same shape.
    """
    return str(secrets.randbelow(10**9))


def md5_digest(challenge: str, secret: str) -> bytes:
    """Return the raw 16-byte MD5 of ``challenge + secret``.

    This digest is also the seed for AES call encryption keys.
    """
    return hashlib.md5((challenge + secret).encode("utf-8"), usedforsecurity=False).digest()


def md5_response(challenge: str, secret: str) -> str:
    """Return the MD5_RESULT value for ``challenge`` and ``secret`` (lowercase hex)."""
    return md5_digest(challenge, secret).hex()


# --------------------------------------------------------------------------
# RSA
# --------------------------------------------------------------------------


def _require_crypto() -> Any:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise AuthenticationError(
            "RSA authentication requires the 'cryptography' package (pip install iax2[crypto])"
        ) from exc
    return hashes, serialization, padding


def rsa_sign(private_key: RSAPrivateKey, challenge: str) -> str:
    """Sign ``challenge`` and return the base64 RSA_RESULT value.

    Uses PKCS#1 v1.5 padding with SHA-1, as Asterisk's ``ast_sign()`` does.
    """
    hashes, _, padding = _require_crypto()
    sig = private_key.sign(challenge.encode("utf-8"), padding.PKCS1v15(), hashes.SHA1())
    return base64.b64encode(sig).decode("ascii")


def rsa_verify(public_key: RSAPublicKey, challenge: str, response: str) -> bool:
    """Return ``True`` if ``response`` is a valid signature of ``challenge``."""
    hashes, _, padding = _require_crypto()
    from cryptography.exceptions import InvalidSignature

    try:
        sig = base64.b64decode(response, validate=False)
        public_key.verify(sig, challenge.encode("utf-8"), padding.PKCS1v15(), hashes.SHA1())
    except (InvalidSignature, ValueError):
        return False
    return True


class RSAKeyStore:
    """Loads Asterisk-style RSA keys by name.

    Asterisk keeps keys in ``/var/lib/asterisk/keys`` as ``<name>.pub`` (PEM
    public key) and ``<name>.key`` (PEM private key, optionally encrypted).
    ``inkeys``/``outkey`` options name keys without the extension; the same
    convention is used here.

    Args:
        directory: Directory containing the key files.
        passphrases: Optional ``{key name: passphrase}`` for encrypted keys.
    """

    def __init__(
        self,
        directory: str | os.PathLike[str] = "/var/lib/asterisk/keys",
        passphrases: dict[str, str] | None = None,
    ):
        self.directory = Path(directory)
        self.passphrases = dict(passphrases or {})
        self._public: dict[str, RSAPublicKey] = {}
        self._private: dict[str, RSAPrivateKey] = {}

    def public(self, name: str) -> RSAPublicKey:
        """Return the public key ``name`` (from ``<name>.pub``)."""
        if name not in self._public:
            _, serialization, _ = _require_crypto()
            data = (self.directory / f"{name}.pub").read_bytes()
            self._public[name] = serialization.load_pem_public_key(data)
        return self._public[name]

    def private(self, name: str) -> RSAPrivateKey:
        """Return the private key ``name`` (from ``<name>.key``)."""
        if name not in self._private:
            _, serialization, _ = _require_crypto()
            data = (self.directory / f"{name}.key").read_bytes()
            pw = self.passphrases.get(name)
            self._private[name] = serialization.load_pem_private_key(
                data, password=pw.encode() if pw else None
            )
        return self._private[name]

    def add_public(self, name: str, key: RSAPublicKey) -> None:
        """Register an in-memory public key under ``name``."""
        self._public[name] = key

    def add_private(self, name: str, key: RSAPrivateKey) -> None:
        """Register an in-memory private key under ``name``."""
        self._private[name] = key


# --------------------------------------------------------------------------
# Credentials and high-level helpers
# --------------------------------------------------------------------------


@dataclass
class Credentials:
    """Secrets used to authenticate to, or verify, a peer.

    Attributes:
        username: Username sent in the USERNAME IE.
        secret: Shared secret for MD5/plaintext. On the verifying side
            several secrets may be given separated by ``;`` (Asterisk syntax).
        private_key: Our RSA private key (client side, Asterisk ``outkey``).
        public_keys: Acceptable RSA public keys (server side, ``inkeys``).
        methods: Methods the *verifier* will offer. Defaults to MD5 plus RSA
            when public keys are configured.
    """

    username: str | None = None
    secret: str | None = None
    private_key: RSAPrivateKey | None = None
    public_keys: list[RSAPublicKey] = field(default_factory=list)
    methods: AuthMethod | None = None

    def offered_methods(self) -> AuthMethod:
        """The AUTHMETHODS value a server should advertise for these credentials."""
        if self.methods is not None:
            return self.methods
        m = AuthMethod.NONE
        if self.secret:
            m |= AuthMethod.MD5
        if self.public_keys:
            m |= AuthMethod.RSA
        return m


def build_auth_reply(
    creds: Credentials, methods: int, challenge: str | None
) -> tuple[IEs, AuthMethod]:
    """Build the IEs answering an AUTHREQ/REGAUTH, choosing a method like Asterisk.

    Args:
        creds: Our credentials.
        methods: The peer's AUTHMETHODS bit mask.
        challenge: The peer's CHALLENGE text.

    Returns:
        ``(ies, method)`` — the IEs to append (one of RSA_RESULT, MD5_RESULT,
        PASSWORD) and the method used.

    Raises:
        AuthenticationError: if no offered method can be satisfied.
    """
    ies = IEs()
    if creds.private_key is not None and methods & AuthMethod.RSA and challenge:
        ies.add(IE.RSA_RESULT, rsa_sign(creds.private_key, challenge))
        return ies, AuthMethod.RSA
    if creds.secret:
        if methods & AuthMethod.MD5 and challenge:
            ies.add(IE.MD5_RESULT, md5_response(challenge, creds.secret))
            return ies, AuthMethod.MD5
        if methods & AuthMethod.PLAINTEXT:
            ies.add(IE.PASSWORD, creds.secret)
            return ies, AuthMethod.PLAINTEXT
    raise AuthenticationError(f"no way to authenticate (peer offers methods {int(methods)})")


def verify_auth_reply(creds: Credentials, challenge: str, ies: IEs) -> AuthMethod | None:
    """Check an AUTHREP/REGREQ reply against our credentials.

    Follows Asterisk's ``authenticate_verify()`` precedence: RSA if offered
    and an RSA_RESULT is present, else MD5 if offered, else plaintext.

    Returns:
        The method that succeeded, or ``None`` if authentication failed.
    """
    offered = creds.offered_methods()
    rsa_result = ies.get(IE.RSA_RESULT)
    if offered & AuthMethod.RSA and rsa_result and creds.public_keys:
        for key in creds.public_keys:
            if rsa_verify(key, challenge, rsa_result):
                return AuthMethod.RSA
        return None
    if offered & AuthMethod.MD5:
        got = (ies.get(IE.MD5_RESULT) or "").lower()
        for secret in (creds.secret or "").split(";"):
            if got and hmac.compare_digest(md5_response(challenge, secret), got):
                return AuthMethod.MD5
        return None
    if offered & AuthMethod.PLAINTEXT:
        pw = ies.get(IE.PASSWORD)
        if pw is not None and creds.secret is not None and hmac.compare_digest(pw, creds.secret):
            return AuthMethod.PLAINTEXT
    return None
