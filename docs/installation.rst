Installation
============

Requirements
------------

* Python 3.11 or newer.
* No mandatory dependencies.
* Optional: `cryptography <https://cryptography.io>`_ for RSA authentication
  and AES call encryption.

From PyPI
---------

.. code-block:: console

    $ pip install iax2              # core
    $ pip install "iax2[crypto]"    # + RSA authentication and encryption

From source
-----------

.. code-block:: console

    $ git clone https://github.com/AllStarLink/py-iax2
    $ cd py-iax2
    $ pip install .

Debian / Ubuntu package
-----------------------

The source tree contains a ``debian/`` directory using ``pybuild``:

.. code-block:: console

    $ sudo apt install debhelper dh-python pybuild-plugin-pyproject python3-hatchling
    $ dpkg-buildpackage -us -uc -b
    $ sudo apt install ../python3-iax2_*.deb

The package installs the ``iax2`` module for the system Python and the
``iax2-tool`` command. ``python3-cryptography`` is a recommended dependency.

Checking the installation
-------------------------

.. code-block:: console

    $ iax2-tool --version
    $ iax2-tool poke 127.0.0.1          # if an Asterisk runs locally
