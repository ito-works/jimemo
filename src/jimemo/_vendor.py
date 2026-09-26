"""Puts the repo's vendor/ directory on sys.path.

Users never pip-install jimemo's dependencies; all runtime imports beyond
the stdlib come from vendor/. Call add_vendor_to_path() before importing
jinja2/markupsafe/markdown anywhere in this package.
"""
import os
import sys
from pathlib import Path

from ._paths import REPO_ROOT

VENDOR_DIR = REPO_ROOT / "vendor"


def _bytecode_cache_dir() -> Path:
    """A per-user cache for bytecode, outside any jimemo checkout."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "jimemo" / "pycache"
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "jimemo" / "pycache"


def add_vendor_to_path() -> None:
    # Keep CPython's bytecode cache out of vendor/: a __pycache__/ dir in
    # there is an unlisted file in the SHA256SUMS scan, and a planted one
    # would be imported INSTEAD of the listed .py. sys.pycache_prefix
    # redirects writes AND reads, so a vendor/**/__pycache__/*.pyc is
    # never even consulted. A caller's own setting -- or a user's exported
    # PYTHONPYCACHEPREFIX, already installed at interpreter startup -- is
    # left alone.
    if sys.pycache_prefix is None:
        sys.pycache_prefix = str(_bytecode_cache_dir())
    vendor = str(VENDOR_DIR)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
