"""Parse and validate a content file against a template's manifest.

Content file formats:
    .md          YAML frontmatter (``---`` ... ``---``) holds every slot
                 value except the markdown body, which is everything
                 after the closing delimiter and fills the slot named
                 ``body``.
    .json        a single JSON object keyed by slot name.
    .yaml/.yml   a single YAML mapping keyed by slot name.

Unknown keys and missing required slots are reported by name so authors
can fix a typo without guessing. Markdown-typed slot values are rendered
to HTML here, sanitized (allowlist — see sanitize.py; python-markdown
passes raw HTML through verbatim, and content may come from untrusted
sources), and returned as ``markupsafe.Markup`` so the (autoescaping)
render step passes them through unescaped; every other slot value is
returned as parsed/raw and relies on Jinja2 autoescape for safety.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ._vendor import add_vendor_to_path
from .errors import ContentError
from .sanitize import sanitize_html

add_vendor_to_path()
import markdown  # noqa: E402
import yaml  # noqa: E402
from markdown.extensions import Extension  # noqa: E402
from markdown.extensions.fenced_code import FencedCodeExtension  # noqa: E402
from markdown.extensions.tables import TableExtension  # noqa: E402
from markdown.treeprocessors import Treeprocessor  # noqa: E402
from markupsafe import Markup  # noqa: E402


def markdown_extensions() -> list:
    """Extension OBJECTS, never name strings. For any string -- even a
    fully qualified dotted path -- Markdown's build_extension scans
    installed entry points BEFORE trying the dotted import
    (vendor/markdown/core.py), and on Python < 3.10 that scan imports
    the importlib_metadata backport, which jimemo does not vendor.
    Stock macOS Python 3.9.6 -- the documented floor until jimemo#gaga
    raised it to 3.13.6 -- crashed on first render exactly there. Objects
    skip name resolution entirely, which is the right thing to pass
    regardless of version, so this stays (jimemo#adw9).
    Fresh instances per call: Extension objects carry per-run config
    and are not documented as reuse-safe across Markdown instances."""
    return [TableExtension(), FencedCodeExtension(), JapaneseSoftBreakExtension()]


# jimemo#saa4: a "\n" inside prose text renders as a space, and Japanese
# has no inter-word spaces, so a hard-wrapped paragraph showed a visible
# gap ("外部の 承認待ち"). A soft break is dropped when BOTH characters
# adjacent to it are Japanese; anything else on either side -- a Latin
# letter, digit, ASCII space or punctuation, Hangul (Korean is written
# with spaces) -- keeps the newline. Code is excluded by structure, not
# by a regex over the source: the treeprocessor below never descends
# into `pre`/`code`, so fenced and indented code blocks and inline code
# spans keep every newline even when Japanese sits on both sides.
_JAPANESE_RANGES = (
    "\u3000-\u303f"              # CJK symbols and punctuation: 、。「」
    "\u3040-\u309f"              # Hiragana
    "\u30a0-\u30ff"              # Katakana
    "\u31f0-\u31ff"              # Katakana phonetic extensions
    "\u3400-\u4dbf"              # Han, extension A
    "\u4e00-\u9fff"              # Han, unified ideographs
    "\uf900-\ufaff"              # Han, compatibility ideographs
    "\U00020000-\U0002fa1f"      # Han, supplements
    "\uff00-\uff9f"              # fullwidth forms and halfwidth katakana: （）！？０ ｶﾅ
    "\uffe0-\uffef"              # fullwidth signs ￥￣ and halfwidth symbol variants; U+FFA0-FFDF (halfwidth Hangul) stays out: Korean keeps its breaks
)
_JAPANESE_SOFT_BREAK_RE = re.compile(
    f"(?<=[{_JAPANESE_RANGES}])\n(?=[{_JAPANESE_RANGES}])"
)
_JAPANESE_CHAR_RE = re.compile(f"[{_JAPANESE_RANGES}]")
# Inline elements whose text reads as part of the surrounding prose, so a
# soft break on either side of one is judged across the node boundary
# (承認は**必要**\nです puts 必要 in a <strong> and \nです in its tail).
# Block children are left alone: the "\n" between two <li> is prettify's
# formatting, not a soft break.
_INLINE_TAGS = frozenset((
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "del", "dfn", "em", "i",
    "ins", "kbd", "mark", "q", "s", "samp", "small", "span", "strong", "sub",
    "sup", "time", "u", "var",
))


def _is_japanese(ch: "str | None") -> bool:
    return bool(ch) and _JAPANESE_CHAR_RE.match(ch) is not None


def _first_char(element) -> "str | None":
    """First rendered character of an inline element's subtree, or None
    when it renders none (a <br />, an empty span)."""
    if element.tag not in _INLINE_TAGS:
        return None
    if element.text:
        return element.text[0]
    for child in element:
        ch = _first_char(child)
        if ch is not None:
            return ch
        if child.tail:
            return child.tail[0]
    return None


def _last_char(element) -> "str | None":
    """Last rendered character of an inline element's subtree, or None."""
    if element.tag not in _INLINE_TAGS:
        return None
    for child in reversed(list(element)):
        if child.tail:
            return child.tail[-1]
        ch = _last_char(child)
        if ch is not None:
            return ch
    if element.text:
        return element.text[-1]
    return None


class JapaneseSoftBreakTreeprocessor(Treeprocessor):
    """Drop soft line breaks between two Japanese characters (jimemo#saa4).

    Registered below 'inline' so code spans are already `code` elements
    by the time it runs, and above 'unescape'; skipping the `pre`/`code`
    subtrees (a tail belongs to the parent's content, so it is still
    joined from there) keeps code byte-identical. Adjacency is judged
    across an INLINE child's boundary too (承認は**必要**\nです), never
    across a block child's: the "\n" prettify puts between two <li> is
    formatting and stays. A hard break is a
    `<br />` element, not text, and survives with its following text
    intact; the "\n" 'prettify' leaves at the start of a tail has no
    character before it and is kept."""

    def run(self, root):
        self._join(root)

    def _join(self, element):
        if element.tag in ("pre", "code"):
            return
        if element.text:
            element.text = _JAPANESE_SOFT_BREAK_RE.sub("", element.text)
        children = list(element)
        for i, child in enumerate(children):
            # A "\n" ending the segment before an inline child, with
            # Japanese on both sides of the node boundary, is a soft break.
            before = element.text if i == 0 else children[i - 1].tail
            if before and before.endswith("\n") and _is_japanese(_first_char(child)):
                prev_ch = before[-2] if len(before) > 1 else (
                    _last_char(children[i - 1]) if i > 0 else None)
                if _is_japanese(prev_ch):
                    before = before[:-1]
                    if i == 0:
                        element.text = before
                    else:
                        children[i - 1].tail = before
            self._join(child)
            if child.tail:
                child.tail = _JAPANESE_SOFT_BREAK_RE.sub("", child.tail)
                # A "\n" starting an inline child's tail, with the child's
                # last character and the tail's next character both Japanese.
                if child.tail.startswith("\n") and _is_japanese(_last_char(child)):
                    next_ch = child.tail[1] if len(child.tail) > 1 else (
                        _first_char(children[i + 1]) if i + 1 < len(children) else None)
                    if _is_japanese(next_ch):
                        child.tail = child.tail[1:]


class JapaneseSoftBreakExtension(Extension):
    def extendMarkdown(self, md):
        md.treeprocessors.register(
            JapaneseSoftBreakTreeprocessor(md), "japanese_soft_break", 5
        )


_TABLE_TAG_RE = re.compile(r"</?table>")
TABLE_SCROLL_OPEN = '<div class="jm-prose__table-scroll">'


def _wrap_tables(html: str) -> str:
    """Wrap each top-level ``<table>`` in a scroll box (jimemo#s3e6), so a
    table wider than a phone scrolls inside it instead of widening the
    page (which made a phone zoom out until the prose was unreadable).
    A wrapper keeps the table a real table to assistive tech, which
    ``display: block`` on the table itself may not. The sanitizer
    serializes every table tag as exactly ``<table>``/``</table>`` but
    can pass an unclosed or stray one through; any imbalance leaves the
    whole fragment unwrapped, so a wrapper never closes an element the
    fragment did not open."""
    depth = 0
    for match in _TABLE_TAG_RE.finditer(html):
        depth += -1 if match.group(0) == "</table>" else 1
        if depth < 0:
            return html
    if depth != 0:
        return html

    def wrap(match: "re.Match[str]") -> str:
        nonlocal depth
        if match.group(0) == "<table>":
            depth += 1
            return TABLE_SCROLL_OPEN + "<table>" if depth == 1 else "<table>"
        depth -= 1
        return "</table></div>" if depth == 0 else "</table>"

    return _TABLE_TAG_RE.sub(wrap, html)


def _render_markdown(text: str) -> Markup:
    # Sole markdown->HTML path (top-level markdown slots AND markdown
    # items in data slots), so sanitizing here covers both. Runs before
    # inline_images, so authored img src are still paths/URLs.
    return Markup(_wrap_tables(
        sanitize_html(markdown.markdown(text, extensions=markdown_extensions()))
    ))


def _coerce_text(path: Path, slot_name: str, value: Any) -> str:
    """YAML's safe_load auto-parses unquoted ISO-date-looking scalars
    (e.g. `date: 2026-07-05`) into datetime.date/datetime objects, and
    bare true/false/numbers into bool/int/float. A manifest "text" slot
    should reliably be a string regardless, so coerce any scalar and
    reject structured values outright."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        raise ContentError(
            f"{path}: slot {slot_name!r} must be text, got {type(value).__name__}"
        )
    return str(value)


def _parse_frontmatter(path: Path, text: str) -> Tuple[Dict[str, Any], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text

    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        raise ContentError(
            f"{path}: unterminated frontmatter block (missing closing '---')"
        )

    fm_text = "".join(lines[1:closing])
    body = "".join(lines[closing + 1:])

    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        # str(e) on a MarkedYAMLError includes the line/column and problem.
        raise ContentError(f"{path}: invalid YAML frontmatter: {e}") from e
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ContentError(f"{path}: frontmatter must be a YAML mapping")

    return parsed, body


def _load_raw(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ContentError(f"cannot read content file {path}: {e}") from e

    if suffix == ".md":
        values, body_md = _parse_frontmatter(path, text)
        if "body" in values:
            raise ContentError(
                f"{path}: 'body' is reserved for the markdown body below the "
                "frontmatter in .md content files and cannot also be set "
                "as a frontmatter key"
            )
        body_md = body_md.strip("\n")
        if body_md:
            values["body"] = body_md
        return values

    if suffix in (".yaml", ".yml"):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ContentError(f"{path}: invalid YAML: {e}") from e
    elif suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ContentError(f"{path} is not valid JSON: {e}") from e
    else:
        raise ContentError(f"unsupported content file type: {path.suffix!r} ({path})")

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ContentError(f"{path}: content must be an object keyed by slot name")
    return data


def _load_data_slot(
    path: Path, slot_name: str, slot_spec: Dict[str, Any], value: Any
) -> List[Any]:
    if not isinstance(value, list):
        raise ContentError(f"{path}: slot {slot_name!r} must be a list of items")

    items_schema = slot_spec.get("items")
    if not items_schema:
        return value

    rendered: List[Any] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ContentError(f"{path}: slot {slot_name!r} item {idx} must be an object")
        for key in item:
            if key not in items_schema:
                raise ContentError(
                    f"{path}: slot {slot_name!r} item {idx} has unknown key {key!r}"
                )
        new_item: Dict[str, Any] = {}
        for key, val in item.items():
            if val is None:
                continue
            if items_schema[key] == "markdown":
                if not isinstance(val, str):
                    raise ContentError(
                        f"{path}: slot {slot_name!r} item {idx} key {key!r} "
                        "must be text"
                    )
                new_item[key] = _render_markdown(val)
            else:
                new_item[key] = _coerce_text(path, f"{slot_name}[{idx}].{key}", val)
        rendered.append(new_item)
    return rendered


def load_content(path: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(path)
    raw = _load_raw(path)
    slots: Dict[str, Any] = manifest.get("slots", {})

    for key in raw:
        if key not in slots:
            raise ContentError(f"{path}: unknown slot {key!r} (not declared in manifest)")

    for slot_name, slot_spec in slots.items():
        if slot_spec.get("required") and (slot_name not in raw or raw[slot_name] is None):
            raise ContentError(f"{path}: missing required slot {slot_name!r}")

    result: Dict[str, Any] = {}
    for slot_name, value in raw.items():
        if value is None:
            continue
        slot_spec = slots[slot_name]
        slot_type = slot_spec["type"]
        if slot_type == "markdown":
            if not isinstance(value, str):
                raise ContentError(f"{path}: slot {slot_name!r} must be text (markdown)")
            result[slot_name] = _render_markdown(value)
        elif slot_type == "data":
            result[slot_name] = _load_data_slot(path, slot_name, slot_spec, value)
        else:  # text
            result[slot_name] = _coerce_text(path, slot_name, value)

    return result
