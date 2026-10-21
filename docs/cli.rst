``iax2-tool`` command line
==========================

``iax2-tool`` (also ``python3 -m iax2``) exercises IAX2 peers from the shell.

.. code-block:: text

    iax2-tool [-v|-vv] [-b ADDR[:PORT]] COMMAND ...

``-v`` logs protocol events, ``-vv`` also every frame sent and received.
``-b`` sets the local address (default ``0.0.0.0:0``, any free port).

poke
----

.. code-block:: console

    $ iax2-tool poke pbx.example.org -c 3
    PONG from 192.0.2.10:4569: time=12.3 ms

call
----

.. code-block:: console

    $ iax2-tool call pbx.example.org 600 -u me -s pw [-C context] \
          [--play in.wav | --tone 440] [--record out.wav] [--dtmf 123#] \
          [--text hi] [--codecs alaw,ulaw] [-d seconds]

Prints DTMF, text and the hangup cause as they arrive.

serve
-----

.. code-block:: console

    $ iax2-tool -b :4569 serve [-u alice:pw ...] [--guest] \
          [--play file.wav | --tone 1000] [--ring 2] [--registrar] [--no-calltoken]

Answers every call: echoes audio back by default, or plays a file/tone.

Give at least one ``-u`` user or ``--guest``; ``--guest`` accepts callers
without a known username, with no authentication.

register
--------

.. code-block:: console

    $ iax2-tool register pbx.example.org myphone pw [--refresh 60] [--once]

decode
------

.. code-block:: console

    $ iax2-tool decode 8123 0000 01020304 05 06 06 01 0b02 0002
    IAX/NEW 291->0 ts=16909060 o=5 i=6 {'VERSION': 2}
