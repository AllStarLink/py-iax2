AllStarLink (app_rpt)
=====================

AllStarLink nodes are Asterisk running ``app_rpt``; nodes link to each
other and clients connect to them over IAX2. py-iax2 is tested against an
ASL3 node.

Connecting like iaxRpt
----------------------

ASL3's ``iax.conf`` defines an ``[iaxrpt](!)`` template whose context runs
``Rpt(node,X)``. Create a user from it (``/etc/asterisk/custom/iax/iaxrpt-users.conf``)::

    [myclient](iaxrpt)
    secret = s3cret

Then:

.. code-block:: python

    call = await ep.dial("node.example.org", "2000", username="myclient",
                         secret="s3cret", caller_number="N0CALL")
    call.radio_key()                  # PTT: CONTROL RADIO_KEY
    await call.play_pcm(pcm)
    call.radio_unkey()                # CONTROL RADIO_UNKEY

The node sends ``RADIO_KEY``/``RADIO_UNKEY`` controls (as
:class:`~iax2.ControlEvent`) when it transmits, audio while it does, and
``TEXT`` frames such as the ``"L ..."`` link list. The caller ID must not be
the node's own number ("Trying to link to self?").
``examples/allstar_client.py`` is a complete client.

Notes
-----

* ``iaxRpt``-type users have ``requirecalltoken = no``; the library supports
  call tokens anyway.
* ASL3 enables ``jitterbuffer`` and ``forcejitterbuffer``; see the DTMF
  note in :doc:`calls`.
* Node-to-node links (context ``radio-secure``) use the ``radio`` user and
  per-node registration/authentication handled by ASL; the protocol side is
  the same as any IAX2 call, but app_rpt expects its own text-message
  conventions — consult the app_rpt source if you want to emulate a node.
