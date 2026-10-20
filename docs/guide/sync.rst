Blocking API
============

:mod:`iax2.sync` runs the asyncio machinery in a background thread and
exposes blocking methods, for scripts, REPL use and threaded applications:

.. code-block:: python

    from iax2.sync import SyncEndpoint
    from iax2 import audio

    with SyncEndpoint(port=0) as ep:
        print(ep.poke("pbx.example.org"))
        call = ep.dial("pbx.example.org", "600", username="me", secret="pw")
        call.play_pcm(audio.tone(440, 1000))
        for ev in call.events():
            print(ev)
        # call.hangup() if the far end didn't

    # server side
    with SyncEndpoint(port=4569) as ep:
        while True:
            call = ep.accept_call()
            call.answer()
            for frame in call.audio():
                call.send_audio(frame.payload)

:class:`~iax2.sync.SyncCall` mirrors :class:`~iax2.Call`; the asynchronous
call is available as ``sync_call.call`` and any coroutine can be run with
:meth:`SyncEndpoint.run() <iax2.sync.SyncEndpoint.run>`. Callbacks run in
the event-loop thread. Don't call blocking methods from inside callbacks.
