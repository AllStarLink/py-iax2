Development
===========

Layout
------

.. code-block:: text

    src/iax2/          the package
    tests/             unit and loopback tests (no network needed)
    tests/interop/     tests against a real Asterisk
    examples/          example programs
    docs/              this documentation (Sphinx)
    debian/            Debian packaging

Running the tests
-----------------

.. code-block:: console

    $ pip install -e ".[dev]"
    $ pytest                      # unit + loopback tests

Interoperability tests
----------------------

These run against a **local** Asterisk (an ASL3 node works) and change its
configuration — never use them on a production system.

.. code-block:: console

    $ sudo python3 tests/interop/setup_asterisk.py      # install test users/dialplan
    $ pytest -m interop tests/interop
    $ sudo python3 tests/interop/setup_asterisk.py --remove

``setup_asterisk.py`` writes ``/etc/asterisk/custom/iax.conf`` and
``custom/extensions.conf`` (both ``#tryinclude``\ d by ASL3), installs an RSA
test key, loads the dialplan applications the tests need and reloads
chan_iax2. The tests use ``sudo -n asterisk -rx`` for CLI commands, so
passwordless sudo is required.

Code style
----------

``ruff check`` and ``mypy`` are configured in ``pyproject.toml``. Public
APIs carry type hints and Google-style docstrings (rendered here by
``sphinx.ext.napoleon``).

Building the docs
-----------------

.. code-block:: console

    $ sphinx-build -W -b html docs docs/_build/html
