# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""AES-128 call encryption, compatible with Asterisk's ``encryption=aes128``.

Asterisk's scheme (``encrypt_frame()`` / ``decode_frame()`` in chan_iax2.c):

* The key is the raw MD5 digest of ``challenge + secret`` from the MD5
  authentication exchange — so encryption requires MD5 authentication.
* Everything after the call numbers (4 bytes of a full frame, 2 bytes of a
  mini frame) is encrypted with AES-128 in CBC mode with an all-zero IV.
* The plaintext is prefixed with 16–31 bytes of random padding so its length
  is a multiple of 16; the low nibble of the 16th byte holds the padding
  length minus 16.
* There is no integrity protection; a wrong key yields garbage.
* With ``KEYROTATE``, the side sending voice periodically sends RTKEY with a
  new random 16-byte key (in a CHALLENGE IE) and switches its *transmit* key;
  the receiver switches its *receive* key.

Negotiation: the caller offers methods in the ENCRYPTION IE of NEW; the
callee echoes the common subset in AUTHREQ and from then on expects every
frame from the caller to be encrypted (the AUTHREP is the first). The callee
starts encrypting once it has learned the key.

Requires the optional ``cryptography`` package (``pip install iax2[crypto]``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import TYPE_CHECKING, Any

from .constants import IE, Encryption, IAXCommand
from .exceptions import EncryptionError
from .ies import IEs

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session

__all__ = ["CallCrypto", "available", "decrypt_datagram", "encrypt_datagram"]

log = logging.getLogger("iax2.crypto")

#: Methods this library offers when encryption is enabled.
SUPPORTED = int(Encryption.AES128 | Encryption.KEYROTATE)


def available() -> bool:
    """``True`` if the ``cryptography`` package is installed."""
    try:
        import cryptography.hazmat.primitives.ciphers as _ciphers
    except ImportError:  # pragma: no cover - environment dependent
        return False
    return _ciphers is not None


def _cipher(key: bytes) -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover
        raise EncryptionError("call encryption requires the 'cryptography' package") from exc
    return Cipher(algorithms.AES(key), modes.CBC(bytes(16)))


def encrypt_datagram(key: bytes, data: bytes) -> bytes:
    """Encrypt a serialised full or mini frame with ``key``."""
    clear = 4 if data[0] & 0x80 else 2
    body = data[clear:]
    pad_len = 16 - (len(body) % 16)
    pad_len = 16 + (pad_len & 0x0F)
    pad = bytearray(os.urandom(pad_len))
    pad[15] = (pad[15] & 0xF0) | (pad_len & 0x0F)
    enc = _cipher(key).encryptor()
    out: bytes = data[:clear] + enc.update(bytes(pad) + body) + enc.finalize()
    return out


def decrypt_datagram(key: bytes, data: bytes) -> bytes | None:
    """Decrypt a full or mini frame; ``None`` if the length is invalid."""
    clear = 4 if data[0] & 0x80 else 2
    body = data[clear:]
    hdr = 12 if clear == 4 else 4
    if len(body) < 16 or len(body) % 16:
        return None
    dec = _cipher(key).decryptor()
    pt: bytes = dec.update(body) + dec.finalize()
    padding = 16 + (pt[15] & 0x0F)
    if len(data) < padding + hdr:
        return None
    return data[:clear] + pt[padding:]


class CallCrypto:
    """Encryption state of one session.

    Attributes:
        methods: Negotiated ENCRYPTION bits.
        tx_key: Key for frames we send (``None`` = send in clear for now).
        rx_key: Key for frames we receive (``None`` = still being determined).
        candidates: Possible ``challenge+secret`` digests while the callee is
            waiting to learn which secret the caller used.
    """

    def __init__(self, methods: int):
        self.methods = methods
        self.tx_key: bytes | None = None
        self.rx_key: bytes | None = None
        self.candidates: list[bytes] = []
        self._rotate_handle: Any = None

    @property
    def rx_active(self) -> bool:
        """Incoming frames are expected to be encrypted."""
        return self.rx_key is not None or bool(self.candidates)

    def encrypt(self, data: bytes) -> bytes:
        """Encrypt an outgoing datagram (no-op until a key is known)."""
        if self.tx_key is None:
            return data
        return encrypt_datagram(self.tx_key, data)

    def decrypt(self, data: bytes) -> bytes | None:
        """Decrypt an incoming datagram, learning the key if necessary."""
        if self.rx_key is not None:
            return decrypt_datagram(self.rx_key, data)
        from .exceptions import FrameError
        from .frames import FullFrame

        for key in self.candidates:
            plain = decrypt_datagram(key, data)
            if plain is None:
                continue
            if data[0] & 0x80:
                # Pick the secret whose decryption yields a well-formed frame;
                # Asterisk simply takes the first secret.
                try:
                    FullFrame.from_bytes(plain).ies  # noqa: B018
                except FrameError:
                    if len(self.candidates) > 1:
                        continue
            self.rx_key = self.tx_key = key
            self.candidates = []
            return plain
        return None


def _digest(challenge: str, secret: str) -> bytes:
    return hashlib.md5((challenge + secret).encode(), usedforsecurity=False).digest()


def enable_caller(session: Session, challenge: str, secret: str, methods: int) -> None:
    """Caller side: keys are known once we answer AUTHREQ; encrypt from AUTHREP on."""
    c = CallCrypto(methods)
    c.tx_key = c.rx_key = _digest(challenge, secret)
    session.crypto = c
    _schedule_rotation(session)


def enable_callee(session: Session, challenge: str, secrets_: list[str], methods: int) -> None:
    """Callee side: after AUTHREQ, expect encrypted frames keyed by one of ``secrets_``."""
    c = CallCrypto(methods)
    c.candidates = [_digest(challenge, s) for s in secrets_]
    session.crypto = c
    _schedule_rotation(session)


def _schedule_rotation(session: Session) -> None:
    c = session.crypto
    if c is None or not c.methods & Encryption.KEYROTATE:
        return
    delay = 120 + secrets.randbelow(181)  # Asterisk: 120 s + random(0..180 s)
    c._rotate_handle = session.call_later(delay, _rotate, session)


def _rotate(session: Session) -> None:
    """Send RTKEY with a fresh key, then switch our transmit key (``iax2_key_rotate``)."""
    c = session.crypto
    if c is None or session.destroyed or c.tx_key is None:
        return
    key = hashlib.md5(f"{secrets.randbits(63):X}".encode(), usedforsecurity=False).digest()
    session.send_command(IAXCommand.RTKEY, IEs().add_raw(IE.CHALLENGE, key))
    c.tx_key = key
    _schedule_rotation(session)


def rotate_key(session: Session, ies: IEs) -> None:
    """Handle a received RTKEY: switch our receive key."""
    c = session.crypto
    key = ies.raw(IE.CHALLENGE)
    if c is None or not key or len(key) != 16:
        log.warning("%r: told to rotate key on an unencrypted call", session)
        return
    c.rx_key = key
