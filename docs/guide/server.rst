Accepting calls (servers)
=========================

Receiving calls
---------------

Incoming calls are delivered after authentication and codec negotiation:

.. code-block:: python

    async for call in ep.incoming():     # async iterator
        ...
    call = await ep.accept_call(timeout=30)   # one at a time
    ep.on_call = handler                 # or a callback (may be async)

Who may call: authentication policy
-----------------------------------

The simplest policy is a user table, like ``iax.conf`` users:

.. code-block:: python

    ep = iax2.Endpoint(
        users={
            "alice": iax2.Credentials(secret="pw1"),            # MD5
            "bob": iax2.Credentials(secret="a;b"),              # either secret
            "node": iax2.Credentials(public_keys=[pubkey]),     # RSA (inkeys)
            "kiosk": iax2.Credentials(secret="pw",
                                      methods=iax2.AuthMethod.PLAINTEXT),
            "lobby": iax2.Credentials(),                        # no authentication
        },
        allow_guest=False,      # unknown usernames are rejected
    )

For full control supply an ``authenticator``; it receives the
:class:`~iax2.CallInfo` and the caller's address and returns:

* :class:`~iax2.Credentials` — the caller must authenticate with them;
* ``True`` or ``None`` — admit without authentication;
* ``False`` — reject ("No authority found").

It may be a coroutine, e.g. to look users up in a database:

.. code-block:: python

    async def authenticator(info: iax2.CallInfo, addr):
        row = await db.fetch_user(info.username)
        if row is None:
            return False
        return iax2.Credentials(secret=row.secret)

    ep = iax2.Endpoint(authenticator=authenticator)

Failed authentication is answered with ``REJECT`` (cause
``FACILITY_NOT_SUBSCRIBED``), optionally after one second
(``delay_reject=True``, Asterisk's ``delayreject``).

Call tokens
-----------

By default (``require_calltoken=True``, Asterisk's default) a new caller
must first echo a call token, which proves it can receive at its source
address and defeats spoofed-source floods. Very old clients (iaxRpt,
some hardware) do not support tokens; set ``require_calltoken=False`` to
let them in.

Codec negotiation
-----------------

The endpoint's capability (``capability``) and preference order
(``prefs``) default to every codec with a registered implementation
(μ-law, A-law, slin16, slin). For each call the first of **our**
preferences present in both capabilities is chosen
(``codecpriority=host``); ``caller_first=True`` gives the caller's
preferences priority. A call with no common codec is rejected with
``BEARERCAPABILITY_NOTAVAIL``. :meth:`Call.negotiate_format()
<iax2.Call.negotiate_format>` implements the algorithm if you accept
calls manually.

Information about the caller
----------------------------

:attr:`Call.info <iax2.Call.info>` has everything from the ``NEW``:
``called_number``, ``called_context``, ``calling_number``,
``calling_name``, ``username``, ``language``, ``capability``,
``codec_prefs``, channel ``variables``, ... and the raw IEs.

Dialplan queries
----------------

Peers can ask whether a number exists (``DPREQ``). Answer them with a hook:

.. code-block:: python

    from iax2.constants import DPStatus
    ep.dialplan = lambda call, number: DPStatus.EXISTS if number in routes else DPStatus.NONEXISTENT
