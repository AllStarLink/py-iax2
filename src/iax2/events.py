# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Jason D. McCormick
"""Event and media objects delivered by :class:`~iax2.call.Call`.

Calls report what happens on them as *events* (subclasses of
:class:`CallEvent`), read with :meth:`Call.events() <iax2.call.Call.events>`
or received through a callback. Audio and video are delivered separately as
:class:`AudioFrame` / :class:`VideoFrameData` objects so that a busy media
stream never delays signalling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .call import Call, CallState
    from .codecs import Codec

__all__ = [
    "Accepted",
    "Answered",
    "AudioFrame",
    "CallEvent",
    "ControlEvent",
    "DTMFEvent",
    "FormatChanged",
    "HangupEvent",
    "HTMLEvent",
    "ImageEvent",
    "Progress",
    "QuelchEvent",
    "Ringing",
    "StateChanged",
    "TextEvent",
    "TransferRequest",
    "UnknownFrame",
    "VideoFrameData",
]


@dataclass
class CallEvent:
    """Base class of all call events.

    Attributes:
        call: The call the event belongs to.
    """

    call: Call = field(repr=False)


@dataclass
class StateChanged(CallEvent):
    """The call moved from ``old`` to ``new`` state."""

    old: CallState
    new: CallState


@dataclass
class Accepted(CallEvent):
    """The call was accepted (ACCEPT sent or received); media may now flow.

    Attributes:
        format: The negotiated audio format bit.
    """

    format: int


@dataclass
class Ringing(CallEvent):
    """The far end is ringing (CONTROL RINGING)."""


@dataclass
class Progress(CallEvent):
    """Call progress (CONTROL PROGRESS or PROCEEDING).

    Attributes:
        proceeding: ``True`` for PROCEEDING, ``False`` for PROGRESS.
    """

    proceeding: bool = False


@dataclass
class Answered(CallEvent):
    """The call was answered (CONTROL ANSWER)."""


@dataclass
class HangupEvent(CallEvent):
    """The call ended.

    Attributes:
        cause: Cause text (CAUSE IE), if any.
        cause_code: Q.931 cause code (CAUSECODE IE), if any.
        by_peer: ``True`` if the remote side ended the call.
        reason: Short machine-readable reason: ``"hangup"``, ``"reject"``,
            ``"timeout"``, ``"inval"``, ``"local"``.
    """

    cause: str | None = None
    cause_code: int | None = None
    by_peer: bool = True
    reason: str = "hangup"


@dataclass
class DTMFEvent(CallEvent):
    """A DTMF digit.

    Attributes:
        digit: The digit character (``0-9``, ``*``, ``#``, ``A-D``).
        begin: ``True`` for key-down (DTMF_BEGIN), ``False`` for key-up (DTMF_END).
    """

    digit: str
    begin: bool = False


@dataclass
class TextEvent(CallEvent):
    """A TEXT frame was received."""

    text: str


@dataclass
class HTMLEvent(CallEvent):
    """An HTML frame was received (subclass is an :class:`~iax2.constants.HTMLCommand`)."""

    command: int
    data: bytes


@dataclass
class ImageEvent(CallEvent):
    """An IMAGE frame was received (``format`` is the image format bit)."""

    format: int
    data: bytes


@dataclass
class ControlEvent(CallEvent):
    """A CONTROL frame not covered by a more specific event (hold, radio key, ...).

    Attributes:
        control: The :class:`~iax2.constants.ControlType` value.
        data: Optional payload.
    """

    control: int
    data: bytes = b""


@dataclass
class QuelchEvent(CallEvent):
    """The peer asked us to stop (``quelch=True``) or resume sending media.

    Attributes:
        quelch: ``True`` for QUELCH, ``False`` for UNQUELCH.
        music_on_hold: The peer suggested playing music on hold.
    """

    quelch: bool
    music_on_hold: bool = False


@dataclass
class TransferRequest(CallEvent):
    """The peer asked us to blind-transfer the call (IAX TRANSFER command).

    Attributes:
        number: Destination number.
        context: Destination context, if given.
    """

    number: str
    context: str | None = None


@dataclass
class FormatChanged(CallEvent):
    """The inbound voice format changed (a full voice frame with a new codec)."""

    format: int


@dataclass
class UnknownFrame(CallEvent):
    """A frame of a type the library doesn't interpret."""

    frame_type: int
    subclass: int
    data: bytes


@dataclass
class AudioFrame:
    """A received voice frame.

    Attributes:
        payload: Encoded audio, exactly as received.
        format: Format bit of ``payload``.
        timestamp: Sender's timestamp in ms (reconstructed for mini frames).
        full: ``True`` if it arrived in a full frame.
        codec: Codec used by :attr:`pcm`, if the format is supported.
    """

    payload: bytes
    format: int
    timestamp: int
    full: bool = False
    codec: Codec | None = field(default=None, repr=False)
    lost: bool = False
    """``True`` for a placeholder produced by :meth:`Call.playout` for a lost frame
    (its payload is encoded silence)."""

    @property
    def pcm(self) -> bytes:
        """The audio decoded to 16-bit little-endian PCM.

        Raises:
            CodecError: if no codec is registered for :attr:`format`.
        """
        if self.codec is None:
            from .exceptions import CodecError

            raise CodecError(f"no codec registered for format {self.format:#x}")
        return self.codec.decode(self.payload)

    @property
    def samples(self) -> int | None:
        """Number of samples, if it can be determined."""
        if self.codec is not None:
            try:
                return self.codec.samples(self.payload)
            except Exception:  # noqa: BLE001
                return None
        return None


@dataclass
class VideoFrameData:
    """A received video frame (or slice).

    Attributes:
        payload: Encoded video.
        format: Video format bit.
        timestamp: Sender timestamp in ms.
        mark: End-of-frame marker.
    """

    payload: bytes
    format: int
    timestamp: int
    mark: bool = False
