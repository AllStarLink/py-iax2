# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Exception hierarchy for the :mod:`iax2` package.

Every exception raised deliberately by this library derives from
:class:`IAX2Error`, so applications can catch that single type.
"""

from __future__ import annotations

__all__ = [
    "AuthenticationError",
    "CallError",
    "CallRejected",
    "CallTimeout",
    "CodecError",
    "EncryptionError",
    "FrameError",
    "IAX2Error",
    "IEError",
    "RegistrationError",
    "TransportError",
]


class IAX2Error(Exception):
    """Base class for all errors raised by :mod:`iax2`."""


class FrameError(IAX2Error):
    """A datagram could not be parsed or a frame could not be built."""


class IEError(FrameError):
    """An Information Element is malformed or has the wrong size."""


class TransportError(IAX2Error):
    """The UDP transport is closed or could not send/receive."""


class AuthenticationError(IAX2Error):
    """Authentication failed or no compatible authentication method exists."""


class EncryptionError(IAX2Error):
    """Encryption was required but could not be negotiated or performed."""


class CodecError(IAX2Error):
    """No compatible codec, or encoding/decoding of media failed."""


class CallError(IAX2Error):
    """A call-level operation failed."""


class CallRejected(CallError):
    """The remote side rejected or hung up a call before it was answered.

    Attributes:
        cause: Human-readable cause text from the CAUSE IE, if any.
        cause_code: Q.931 cause code from the CAUSECODE IE, if any.
    """

    def __init__(self, message: str, cause: str | None = None, cause_code: int | None = None):
        super().__init__(message)
        self.cause = cause
        self.cause_code = cause_code


class CallTimeout(CallError):
    """The remote side stopped responding (retransmissions exhausted)."""


class RegistrationError(IAX2Error):
    """Registration was rejected or timed out.

    Attributes:
        cause: Cause text from the REGREJ, if any.
        cause_code: Q.931 cause code from the REGREJ, if any.
    """

    def __init__(self, message: str, cause: str | None = None, cause_code: int | None = None):
        super().__init__(message)
        self.cause = cause
        self.cause_code = cause_code
