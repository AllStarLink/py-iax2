Using with Asterisk
===================

Asterisk calling the library
----------------------------

Define the library as a peer in ``iax.conf``:

.. code-block:: ini

    [pybox]
    type=peer
    host=192.0.2.50          ; where your Endpoint listens
    port=4569
    username=asterisk        ; sent in NEW; must exist in your users table
    secret=pw
    auth=md5
    requirecalltoken=yes
    disallow=all
    allow=ulaw

and dial ``IAX2/pybox/1000``. On the Python side::

    ep = iax2.Endpoint(port=4569, users={"asterisk": iax2.Credentials(secret="pw")},
                       allow_guest=False)

The library calling Asterisk
----------------------------

.. code-block:: ini

    [pyclient]
    type=user                ; or friend + host=dynamic to allow registration
    secret=pw
    auth=md5
    context=from-python
    disallow=all
    allow=ulaw

``await ep.dial("asterisk.host", "1000", username="pyclient", secret="pw")``.
Pass ``context=`` to request a specific context (Asterisk only honours it
if the user is allowed into it).

Registering with Asterisk
-------------------------

Use ``type=friend`` and ``host=dynamic`` for the peer, then
``await ep.register("asterisk.host", "pyclient", "pw")``. Asterisk will
then reach you at the registered address with ``IAX2/pyclient/...``.

Behaviour notes
---------------

* Asterisk's ``codecpriority=host`` (default) picks the codec from *its*
  ``allow=`` order; the library's default for incoming calls is the same.
* Asterisk sends ``slin`` big-endian; the library converts.
* DTMF and text are held by Asterisk's jitter buffer until voice flows
  (see :doc:`calls`).
* ``autoload=no`` systems (ASL3) may lack dialplan applications such as
  ``Echo``; load them with ``module load``.
* Asterisk 22 is built without native bridging, so it never sends
  ``TXREQ`` (but accepts transfers from others).
* The CLI command ``iax2 set debug on`` shows every frame in the Asterisk
  log; ``iax2-tool -vv`` shows the library's side.
