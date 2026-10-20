py-iax2 — IAX2 for Python
=========================

**py-iax2** is a complete, pure-Python implementation of the IAX2 protocol
(Inter-Asterisk eXchange, `RFC 5456 <https://www.rfc-editor.org/rfc/rfc5456>`_)
built on :mod:`asyncio`. It talks to Asterisk — including AllStarLink
``app_rpt`` nodes — and to other IAX2 software, as a client, a server, or both.

Where the RFC is silent or Asterisk differs from it, the library does exactly
what **Asterisk 22 LTS** does, and an interoperability test suite runs it
against a live Asterisk.

.. code-block:: python

    import asyncio
    import iax2

    async def main():
        async with iax2.Endpoint(port=0) as ep:
            call = await ep.dial("pbx.example.org", "600", username="me", secret="pw")
            await call.play_pcm(iax2.audio.tone(440, 2000))
            await call.hangup()

    asyncio.run(main())

Features
--------

* Outgoing and incoming calls with full Asterisk-compatible call setup,
  sequencing, acknowledgement and retransmission.
* Authentication: MD5 challenge/response, RSA signatures (Asterisk key files),
  plaintext; anti-spoofing **call tokens** as client and server.
* **AES-128 call encryption** with key rotation, compatible with Asterisk.
* Media: voice full/mini frames, built-in μ-law, A-law and signed-linear
  codecs (no dependencies), pluggable codecs, adaptive jitter buffer,
  trunking (meta trunk frames), video, DTMF, text, HTML/URL and image frames.
* Registration client and registrar; POKE qualification; PING/LAGRQ
  statistics; dialplan queries; native transfers (endpoint and server roles).
* Blocking wrapper for non-asyncio programs, an ``iax2-tool`` CLI and
  example programs (echo server, mini PBX, AllStarLink client, ...).

.. toctree::
   :maxdepth: 2
   :caption: User guide

   installation
   quickstart
   guide/concepts
   guide/calls
   guide/server
   guide/media
   guide/registration
   guide/security
   guide/transfers
   guide/sync
   guide/allstarlink
   guide/asterisk
   cli

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api
   protocol
   development

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
