# Contributing to py-iax2

Bug reports, interop reports and pull requests are welcome. For security
issues, see [SECURITY.md](SECURITY.md) instead.

## Ground rules

* **Asterisk is the reference.** The behaviour follows RFC 5456, and where the
  RFC and Asterisk disagree or the RFC is silent, we match the current Asterisk
  LTS (`channels/chan_iax2.c`). Please cite the Asterisk function or RFC
  section when changing protocol behaviour.
* **General IAX2 first.** AllStarLink / app_rpt is a primary user, but nothing
  may be added that breaks interoperability with plain Asterisk or other IAX2
  peers. app_rpt-specific behaviour must be opt-in.
* **No mandatory dependencies.** The core is pure Python. Optional features
  (e.g. `cryptography` for RSA/AES) go behind an extra and must degrade cleanly
  when it is missing.

## Development setup

```console
$ python3 -m venv .venv && . .venv/bin/activate
$ pip install -e ".[dev,docs]"
```

## Before opening a pull request

```console
$ ruff check src tests examples
$ ruff format src tests examples
$ mypy --strict src
$ pytest                                  # unit, loopback and robustness tests
$ sphinx-build -W -b html docs docs/_build/html
```

CI runs the same checks. If you change protocol behaviour, please also run the
interop suite against a real Asterisk (see `docs/development.rst`):

```console
$ sudo python3 tests/interop/setup_asterisk.py
$ pytest -m interop tests/interop
```

and add a test: loopback tests in `tests/`, and an interop test when the
change concerns behaviour against Asterisk.

## Style

* Public API is fully type-annotated and documented with Google-style
  docstrings (rendered by Sphinx).
* Keep the asyncio core free of blocking calls; the synchronous API lives in
  `iax2.sync`.
* Add a line to `CHANGELOG.md` under *Unreleased* for user-visible changes.

## Licence

py-iax2 is licensed under GPL-2.0-only, matching Asterisk. By submitting a
contribution you agree that it is licensed under the same terms, and you
confirm you have the right to submit it.
