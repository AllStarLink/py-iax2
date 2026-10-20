Working with calls
==================

Placing calls
-------------

:meth:`Endpoint.dial() <iax2.Endpoint.dial>` builds the ``NEW`` message,
handles call tokens and authentication, and by default returns when the call
is **answered**:

.. code-block:: python

    call = await ep.dial(
        "192.0.2.10", "1000",
        port=4569,
        context="from-iax",            # CALLED_CONTEXT (optional)
        username="me", secret="pw",    # or private_key=... for RSA
        caller_number="5551212", caller_name="Front desk",
        formats=[iax2.Format.ALAW, iax2.Format.ULAW],   # offer, in preference order
        variables={"ACCOUNT": "42"},   # Asterisk IAXVAR() channel variables
        timeout=60,
    )

``wait="accepted"`` returns as soon as the callee has accepted (to hear
ringback or early media); ``wait=None`` returns immediately after sending
``NEW``. In those cases use :meth:`~iax2.Call.wait_answered`.

Answering, rejecting, hanging up
--------------------------------

Incoming calls arrive authenticated and (by default) already **accepted**,
just as Asterisk sends ``ACCEPT`` before the dialplan runs. Then:

.. code-block:: python

    call.ring()                      # CONTROL RINGING to the caller (optional)
    await call.answer()              # CONTROL ANSWER
    call.reject("Busy here", iax2.Cause.USER_BUSY)   # before answering
    await call.hangup(iax2.Cause.NORMAL_CLEARING)    # at any time

With ``EndpointConfig(auto_accept=False)`` incoming calls stay in
``INCOMING`` until you call :meth:`~iax2.Call.accept` (optionally with a
specific format) or :meth:`~iax2.Call.answer`.

When a call ends, :attr:`~iax2.Call.cause` and :attr:`~iax2.Call.cause_code`
hold the reason (Q.931 codes, see :class:`~iax2.Cause`) and a
:class:`~iax2.HangupEvent` is emitted.

Events
------

Iterate :meth:`Call.events() <iax2.Call.events>` or set
:attr:`Call.on_event <iax2.Call.on_event>`:

========================================  ==================================================
Event                                     Meaning
========================================  ==================================================
:class:`~iax2.StateChanged`               state transition
:class:`~iax2.Accepted`                   codec agreed, media may flow
:class:`~iax2.Ringing`                    far end ringing
:class:`~iax2.Progress`                   PROGRESS / PROCEEDING indication
:class:`~iax2.Answered`                   answered
:class:`~iax2.DTMFEvent`                  DTMF key down (``begin=True``) / up
:class:`~iax2.TextEvent`                  text message
:class:`~iax2.ControlEvent`               other indications: BUSY, CONGESTION, HOLD,
                                          UNHOLD, RADIO_KEY, RADIO_UNKEY, FLASH, ...
:class:`~iax2.QuelchEvent`                peer asked us to stop/resume sending media
:class:`~iax2.events.TransferRequest`     peer asked us to blind-transfer
:class:`~iax2.events.FormatChanged`       inbound codec changed
:class:`~iax2.events.HTMLEvent`           HTML/URL frame
:class:`~iax2.events.ImageEvent`          image frame
:class:`~iax2.HangupEvent`                call ended (always the last event)
========================================  ==================================================

Sending signalling
------------------

.. code-block:: python

    await call.send_dtmf("123#", duration=0.1)   # DTMF_BEGIN/DTMF_END pairs
    call.send_text("hello")
    call.send_url("https://example.org/")
    call.indicate(iax2.ControlType.PROGRESS)
    call.hold("default"); call.unhold()
    call.radio_key(); call.radio_unkey()        # AllStarLink PTT
    call.quelch(); call.unquelch()              # ask the peer to stop/resume media
    call.transfer("2000", "default")            # ask the peer to blind-transfer
    status = await call.query_dialplan("1234")  # DPREQ → DPStatus bits

Control types that Asterisk refuses to carry over IAX2 are silently dropped,
as in Asterisk.

.. note::

   Asterisk's IAX2 jitter buffer releases DTMF and other non-voice frames
   only while voice is flowing. Real phones send audio continuously; if you
   send DTMF on an otherwise silent call to Asterisk, stream silence (e.g.
   ``call.play_pcm(audio.silence(5000))`` in a task) at the same time.

Statistics
----------

:attr:`Call.stats <iax2.Call.stats>` is a :class:`~iax2.session.SessionStats`:
frames sent/received, retransmissions, round-trip time (from the PING/PONG
exchange every 21 s), lag (LAGRQ every 10 s), receive jitter, out-of-order
count and the peer's receiver report.
