Concepts
========

Endpoint
--------

An :class:`~iax2.Endpoint` owns one UDP socket (IAX2 normally uses port
4569) and every dialog on it. All IAX2 traffic for any number of calls is
multiplexed over that one socket using **call numbers**: each side picks a
15-bit number for the dialog and tells the other in the first frame.

An endpoint is both a client and a server: it can :meth:`~iax2.Endpoint.dial`
out, accept :meth:`~iax2.Endpoint.incoming` calls, :meth:`~iax2.Endpoint.register`
with servers and act as a registrar, all at once.

Configuration is an :class:`~iax2.EndpointConfig`; any field can be passed
as a keyword argument to the endpoint::

    ep = iax2.Endpoint("0.0.0.0", 4569, users={...}, require_calltoken=False)

Calls
-----

A :class:`~iax2.Call` is one voice call. It moves through
:class:`~iax2.CallState`\ s::

    outgoing:  DIALING → [AUTHENTICATING] → ACCEPTED → [RINGING] → ANSWERED → HUNGUP
    incoming:  INCOMING → [AUTHENTICATING] → ACCEPTED → ANSWERED → HUNGUP

``ACCEPTED`` means the codec has been agreed and media may flow (early
media, ringback); ``ANSWERED`` means the call is connected.

Frames
------

IAX2 has two main kinds of frame:

* **Full frames** carry a 12-byte header with both call numbers, a 32-bit
  timestamp and sequence numbers. All signalling uses them, and they are
  delivered reliably: acknowledged and retransmitted.
* **Mini frames** have a 4-byte header (source call number, low 16 bits of
  the timestamp) and carry voice only, unreliably. The first voice frame,
  every codec change and every 65 seconds (timestamp wrap) a full voice frame
  is sent instead.

You rarely see frames directly. The library handles sequencing,
acknowledgements, retransmission, timestamps and the full/mini choice exactly
as Asterisk does; you send and receive *audio payloads* and *events*. The
wire layer is available in :mod:`iax2.frames` and :mod:`iax2.ies` if you need it.

Information Elements
--------------------

Control messages (``NEW``, ``AUTHREQ``, ``REGREQ``, ...) carry their
parameters as type-length-value *Information Elements*. :class:`~iax2.IEs`
holds them; :attr:`Call.info <iax2.Call.info>` presents the IEs of the call
setup message as a friendly :class:`~iax2.CallInfo`.

Timestamps
----------

Every frame carries a timestamp in milliseconds since the call started.
Voice timestamps advance by the frame duration; other frames are slotted in
between. Received audio frames expose :attr:`AudioFrame.timestamp
<iax2.AudioFrame.timestamp>`, already reconstructed to 32 bits for mini
frames — use it to detect loss and reorder (or let
:meth:`Call.playout() <iax2.Call.playout>` do it).

Concurrency model
-----------------

The library is single-threaded asyncio. Callbacks (``on_event``,
``on_audio``, ``on_call``) run on the event loop and must not block; if a
callback returns a coroutine it is scheduled as a task. Use
:mod:`iax2.sync` from threaded programs.
