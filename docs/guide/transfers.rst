Native transfers
================

When a server bridges two IAX2 calls it can ask the two endpoints to send
media straight to each other and drop out of the path ("native transfer",
``TXREQ`` … ``TXREL``).

As an endpoint (always on)
--------------------------

The library answers transfer requests like Asterisk's
``try_transfer()``/``complete_transfer()``: it contacts the new peer,
verifies the path (``TXCNT``/``TXACC``), reports ``TXREADY`` and on
``TXREL`` silently re-homes the call. Your :class:`~iax2.Call` keeps working;
:attr:`~iax2.Call.peer_addr` changes. Refuse transfers with
``ep.transfers.enabled = False``. Encrypted calls are never transferred.

As a server
-----------

.. code-block:: python

    ep.transfers.start_transfer(call_a, call_b)                  # full release
    ep.transfers.start_transfer(call_a, call_b, media_only=True) # TXMEDIA

Both calls end locally with cause ``"transferred"`` (no ``HANGUP`` is sent)
once both endpoints are ready; if either refuses, ``TXREJ`` is relayed and
both calls continue through you. This works with Asterisk as the endpoints
(tested); Asterisk 22 itself is built without native bridging and never
*initiates* transfers.

Blind transfer requests
-----------------------

A peer can also ask *you* to transfer the call elsewhere (IAX ``TRANSFER``);
it arrives as :class:`~iax2.events.TransferRequest`. Send one with
:meth:`Call.transfer() <iax2.Call.transfer>`.
