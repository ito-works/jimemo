"""The renderer-built tooltip (jimemo#7h7c).

A content author marks text or an image with ``title`` in a markdown slot
(the sanitizer keeps it on ``a``, ``img``, the table cells and the inline
phrasing tags; see sanitize.py). When any content slot carries a non-empty
one, render_page adds jimemo's own tooltip ``<style>`` and ``<script>``
from ``toolkit/tooltip.css`` and ``toolkit/tooltip.js``: fixed text that
content can never supply or change. lint.py accepts that script body
byte-for-byte through ``tooltip_js_text`` -- the same function the
renderer calls -- so the two cannot drift on what "the tooltip script" is.
"""
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ._paths import TOOLTIP_CSS, TOOLTIP_JS
from .errors import ContentError


def tooltip_js_text(path: Path = TOOLTIP_JS) -> str:
    """The tooltip script body, as emitted inside the page's <script>.
    Refuses text that could close the script element or open an HTML
    comment in it (the same breakout invariant chart_lib_inline_text
    enforces for the Chart.js bundle)."""
    text = path.read_text(encoding="utf-8")
    lowered = text.lower()
    if "</script" in lowered or "<!--" in lowered:
        raise ContentError(
            f"{path}: tooltip script contains '</script' or '<!--' and "
            "cannot be inlined"
        )
    return text


def tooltip_css_text(path: Path = TOOLTIP_CSS) -> str:
    """The tooltip stylesheet, as emitted inside the page's <style>.
    Refuses text that could close the style element."""
    text = path.read_text(encoding="utf-8")
    if "</style" in text.lower():
        raise ContentError(
            f"{path}: tooltip stylesheet contains '</style' and cannot be "
            "inlined"
        )
    return text


class _TitleFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found = False

    def handle_starttag(self, tag, attrs):
        if any(name == "title" and value for name, value in attrs):
            self.found = True

    handle_startendtag = handle_starttag


def _markup_has_title(html: str) -> bool:
    finder = _TitleFinder()
    finder.feed(html)
    finder.close()
    return finder.found


def content_has_title(content: Any) -> bool:
    """Whether any markdown-rendered value in parsed `content` carries an
    element with a non-empty ``title``. Markdown slots (top level and in
    data items) arrive as sanitized ``Markup``; text slots are plain
    ``str``, autoescaped by Jinja, and cannot carry an attribute, so only
    ``Markup`` is examined."""
    # Imported here, not at module level: markupsafe is vendored and
    # reaches sys.path through add_vendor_to_path, which render.py and
    # content.py call before they import this module's callers.
    from markupsafe import Markup

    # Iterative, and each container visited once by identity: a data
    # slot is schema-free YAML, whose anchors and aliases can share a
    # container many times over or make one contain itself.
    stack = [content]
    seen = set()
    while stack:
        value = stack.pop()
        if isinstance(value, Markup):
            if _markup_has_title(str(value)):
                return True
        elif isinstance(value, (dict, list, tuple)):
            if id(value) in seen:
                continue
            seen.add(id(value))
            stack.extend(value.values() if isinstance(value, dict) else value)
    return False
