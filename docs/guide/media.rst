Audio, codecs and media
=======================

PCM conventions
---------------

Wherever the library speaks PCM it means **mono, signed 16-bit,
little-endian** ("s16le", the sample format of ordinary WAV files) at the
codec's sample rate (8000 Hz for μ-law, A-law and slin; 16000 Hz for
slin16). :mod:`iax2.audio` has helpers to read/write WAV files, generate
tones, resample and measure levels.

Sending audio
-------------

.. code-block:: python

    call.send_pcm(pcm_20ms)             # encode with the call's codec, send one frame
    call.send_audio(ulaw_payload)       # already-encoded payload in call.format
    await call.play_pcm(long_pcm)       # split into 20 ms frames, paced in real time

Send one frame every 20 ms (``play_pcm`` does this for you). Timestamps,
full vs. mini frames and silence-gap handling follow Asterisk.
Outgoing voice is dropped while the call is not yet accepted or while the
peer has *quelched* us, as Asterisk does.

Receiving audio
---------------

.. code-block:: python

    frame = await call.recv_audio(timeout=1)    # AudioFrame or None
    async for frame in call.audio(): ...        # until hangup
    call.on_audio = callback                    # push style (no queuing)

An :class:`~iax2.AudioFrame` has the encoded ``payload``, its ``format``, the
sender's ``timestamp`` and, if a codec is registered for the format,
``frame.pcm`` (decoded on access).

Frames are delivered as they arrive — possibly out of order, with gaps.
For a smooth stream use the jitter buffer:

.. code-block:: python

    async for frame in call.playout(min_delay=40, max_delay=400):
        speaker.write(frame.pcm)     # a frame every 20 ms; frame.lost = concealed gap

See :class:`~iax2.jitterbuffer.JitterBuffer` to drive one yourself.

Codecs
------

Built in, pure Python: :class:`~iax2.codecs.ULawCodec`,
:class:`~iax2.codecs.ALawCodec`, :class:`~iax2.codecs.SLinCodec` and
:class:`~iax2.codecs.SLin16Codec` (note: IAX2 carries slin **big-endian**;
the codec converts).

Other formats (GSM, G.722, Opus, ...) work without a codec — negotiate them
with ``formats=[...]``/``capability`` and use ``send_audio``/``frame.payload``
— or plug in an implementation:

.. code-block:: python

    from iax2.codecs import Codec, register_codec
    from iax2.formats import Format

    @register_codec
    class G722Codec(Codec):
        format = Format.G722
        sample_rate = 16000

        def __init__(self):
            self._enc, self._dec = make_g722_encoder(), make_g722_decoder()

        def encode(self, pcm: bytes) -> bytes:
            return self._enc.encode(pcm)

        def decode(self, payload: bytes) -> bytes:
            return self._dec.decode(payload)

A new codec instance is made for each call and direction, so codecs may keep
state. Registering a codec adds its format to the default capability of new
endpoints.

Format negotiation helpers live in :mod:`iax2.formats`
(:func:`~iax2.formats.choose_format`, :func:`~iax2.formats.format_info`,
:func:`~iax2.formats.encode_codec_prefs`, ...).

Trunking
--------

IAX2 trunking bundles the audio of many calls to the same host into one
datagram per interval. Receiving trunk frames is automatic. To send them,
list the peer addresses:

.. code-block:: python

    ep = iax2.Endpoint(trunk_peers={("192.0.2.10", 4569)}, trunk_interval=20)

(The Asterisk side needs ``trunk=yes`` for the matching peer/user.)
``trunk_timestamps=True`` sends per-call timestamps (Asterisk
``trunktimestamps=yes``).

Video, images, HTML
-------------------

.. code-block:: python

    call.send_video(h264_slice, iax2.Format.H264, mark=last_slice_of_picture)
    call.on_video = lambda v: decoder.feed(v.payload, v.mark)
    call.send_image(png_bytes, iax2.Format.PNG)
    call.send_url("https://example.org")

Video uses full frames for the first frame and format changes, and meta
video ("mini video") frames otherwise.
