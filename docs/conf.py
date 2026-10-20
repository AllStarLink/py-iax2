# Sphinx configuration for py-iax2.
import os
import sys

sys.path.insert(0, os.path.abspath("../src"))

from iax2 import __version__  # noqa: E402

project = "py-iax2"
author = "Jason D. McCormick"
copyright = "2026, Jason D. McCormick"
release = __version__
version = ".".join(__version__.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]
intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {"members": True, "show-inheritance": True}
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_attr_annotations = True

templates_path: list[str] = []
exclude_patterns = ["_build"]
html_theme = "alabaster"
html_theme_options = {
    "description": "Pure-Python IAX2 for asyncio",
    "fixed_sidebar": True,
}
html_title = f"py-iax2 {release}"
nitpicky = True


# -- Resolve public aliases (``iax2.Call``) to where autodoc documents them
#    (``iax2.call.Call``), so the guides can use the short public names.
import importlib  # noqa: E402

nitpick_ignore = [
    ("py:class", "RSAPrivateKey"),
    ("py:class", "RSAPublicKey"),
    ("py:class", "iax2.sync.T"),
    ("py:class", "iax2.jitterbuffer.F"),
    ("py:class", "Addr"),
    ("py:class", "F"),
    ("py:class", "T"),
    ("py:class", "_LoopThread"),
    ("py:class", "_asyncio.Task"),
    ("py:class", "asyncio.events.TimerHandle"),
    ("py:class", "asyncio.events.AbstractEventLoop"),
    ("py:obj", "iax2.jitterbuffer.F"),
]


def _canonical(target: str) -> str | None:
    parts = target.split(".")
    if parts[0] != "iax2":
        parts = ["iax2", *parts]
    # Walk back to the class/function that has a __module__ and __qualname__.
    probe = importlib.import_module("iax2")
    chain = [probe]
    for name in parts[1:]:
        if not hasattr(probe, name):
            break
        probe = getattr(probe, name)
        chain.append(probe)
    if len(chain) < len(parts):  # trailing instance attribute (documented in Attributes:)
        pass
    for i in range(len(chain) - 1, 0, -1):
        o = chain[i]
        mod = getattr(o, "__module__", None)
        qual = getattr(o, "__qualname__", None)
        if mod and qual and isinstance(mod, str) and mod.startswith("iax2"):
            return ".".join([mod, qual, *parts[i + 1:]])
    return None


def _missing_reference(app, env, node, contnode):
    if node.get("refdomain") != "py":
        return None
    target = node.get("reftarget", "")
    if not target or target.startswith("_"):
        return None
    candidates = [target]
    if "." not in target or not target.startswith("iax2"):
        candidates.append("iax2." + target)
    domain = env.get_domain("py")
    for cand in candidates:
        try:
            full = _canonical(cand)
        except Exception:  # noqa: BLE001
            full = None
        if full and full != target:
            node["reftarget"] = full
            res = domain.resolve_xref(env, node["refdoc"], app.builder, node["reftype"],
                                      full, node, contnode)
            if res is None:
                res = domain.resolve_any_xref(env, node["refdoc"], app.builder, full, node,
                                              contnode)
                res = res[0][1] if res else None
            if res is not None:
                return res
            node["reftarget"] = target
    # Fall back to a unique object whose dotted name ends with the target.
    suffix = "." + target.split("iax2.", 1)[-1] if target.startswith("iax2.") else "." + target
    matches = [name for name in domain.objects if name.endswith(suffix)]
    if len(matches) == 1:
        node["reftarget"] = matches[0]
        res = domain.resolve_any_xref(env, node["refdoc"], app.builder, matches[0], node,
                                      contnode)
        if res:
            return res[0][1]
        node["reftarget"] = target
    return None


def setup(app):
    app.connect("missing-reference", _missing_reference)
