Registration
============

As a client
-----------

Registering tells a server (``host=dynamic`` in Asterisk) where to reach you.
The registration is refreshed automatically at 5/6 of the interval the
server grants:

.. code-block:: python

    reg = await ep.register("pbx.example.org", "myphone", "s3cret", refresh=60)
    print(reg.state, reg.apparent_addr, reg.refresh)
    reg.on_change = lambda r: print("now", r.state)
    ...
    await reg.stop(unregister=True)     # REGREL

:meth:`~iax2.Endpoint.register` waits for the first answer and raises
:class:`~iax2.RegistrationError` if it is rejected. Later failures are
reported through :attr:`~iax2.Registration.state` and retried (every 10 s
after a timeout). ``reg.messages`` holds the ``(new, old)`` voicemail counts
if the server sends them. Endpoints close their registrations on
:meth:`~iax2.Endpoint.close`.

As a registrar
--------------

.. code-block:: python

    users = {"myphone": iax2.Credentials(secret="s3cret")}
    ep = iax2.Endpoint(users=users)
    registrar = ep.enable_registrar()             # uses ep.config.users
    registrar.on_register = lambda e: print(e.username, e.addr, e.expires)
    ...
    addr = registrar.lookup("myphone")            # (host, port) or None
    call = await ep.dial(*addr, "100")

:class:`~iax2.Registrar` mirrors Asterisk: unknown peers are challenged
anyway (so usernames cannot be probed), failures get ``REGREJ``
"Registration Refused", granted intervals are clamped to
``min_refresh``–``max_refresh`` (default 60 s, Asterisk's
``minregexpire``/``maxregexpire``), and entries expire after
refresh + 10 seconds. Set ``registrar.message_counts`` and
``registrar.caller_ids`` to send MSGCOUNT / caller ID in ``REGACK``.
