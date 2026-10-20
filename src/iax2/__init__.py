# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""iax2 — a pure-Python asyncio implementation of the IAX2 protocol.

IAX2 (Inter-Asterisk eXchange, RFC 5456) is the VoIP protocol used between
Asterisk servers and by AllStarLink. This package implements it completely
in Python, interoperating with Asterisk 22 LTS.

Quick start::

    import asyncio
    import iax2

    async def main():
        async with iax2.Endpoint(port=0) as ep:
            call = await ep.dial("127.0.0.1", "1000", username="test", secret="pw")
            await call.play_pcm(b"\\0" * 16000)       # one second of silence
            await call.hangup()

    asyncio.run(main())

The main classes are :class:`Endpoint` (one UDP socket; dials, accepts,
registers) and :class:`Call`. Blocking code can use :mod:`iax2.sync`.
"""

from . import audio
from ._version import __version__
from .auth import Credentials, RSAKeyStore
from .call import Call, CallInfo, CallState, Direction
from .codecs import Codec, get_codec, register_codec
from .constants import (
    DEFAULT_PORT,
    IE,
    AuthMethod,
    Cause,
    ControlType,
    Encryption,
    FrameType,
    IAXCommand,
)
from .endpoint import Endpoint, EndpointConfig
from .events import (
    Accepted,
    Answered,
    AudioFrame,
    CallEvent,
    ControlEvent,
    DTMFEvent,
    HangupEvent,
    Progress,
    QuelchEvent,
    Ringing,
    StateChanged,
    TextEvent,
)
from .exceptions import (
    AuthenticationError,
    CallError,
    CallRejected,
    CallTimeout,
    CodecError,
    FrameError,
    IAX2Error,
    RegistrationError,
    TransportError,
)
from .formats import Format
from .ies import IEs
from .registration import Registrar, Registration, RegistrationState
from .session import Timing

__all__ = [
    "DEFAULT_PORT",
    "IE",
    "Accepted",
    "Answered",
    "AudioFrame",
    "AuthMethod",
    "AuthenticationError",
    "Call",
    "CallError",
    "CallEvent",
    "CallInfo",
    "CallRejected",
    "CallState",
    "CallTimeout",
    "Cause",
    "Codec",
    "CodecError",
    "ControlEvent",
    "ControlType",
    "Credentials",
    "DTMFEvent",
    "Direction",
    "Encryption",
    "Endpoint",
    "EndpointConfig",
    "Format",
    "FrameError",
    "FrameType",
    "HangupEvent",
    "IAX2Error",
    "IAXCommand",
    "IEs",
    "Progress",
    "QuelchEvent",
    "RSAKeyStore",
    "Registrar",
    "Registration",
    "RegistrationError",
    "RegistrationState",
    "Ringing",
    "StateChanged",
    "TextEvent",
    "Timing",
    "TransportError",
    "__version__",
    "audio",
    "get_codec",
    "register_codec",
]
