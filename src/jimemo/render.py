"""Render a manifest-defined template + parsed content into a single,
self-contained HTML page: Jinja2 render -> image inlining -> lint
(fail closed on errors, warn to stderr otherwise).

Charts: when the manifest declares charts, the renderer injects two
extra context names — ``chart_lib`` (the vendored Chart.js source,
emitted once by the page skeleton as the single library <script>) and
``charts`` (one entry per declaration: id, type, title, and the full
init-script body built by charts.chart_init_js as ``init_js``, the only
value the chart macro may be called with). The renderer then hands lint
the exact bodies it emitted (library + every init), and lint requires
the page's inline scripts to equal that set — nothing forged, extra,
duplicated, or missing. A chartless manifest injects neither name,
leaving chartless no-script output byte-identical.
"""
import sys
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ._paths import CHARTJS_BUNDLE, REPO_ROOT
from ._vendor import add_vendor_to_path
from .charts import (
    build_chart_config,
    chart_init_js,
    chart_lib_inline_text,
    serialize_chart_config,
)
from .errors import ContentError
from .inline import (
    _is_remote,
    assemble_css,
    inline_images,
    is_local_file,
    resolve_local_image,
)
from .lint import lint_html
from .manifest import load_manifest
from .sanitize import SvgDrop, _svg_drop_label, sanitize_svg_with_report

add_vendor_to_path()
from jinja2 import (  # noqa: E402
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateError,
    UndefinedError,
)
from markupsafe import Markup  # noqa: E402

TOOLKIT_DIR = REPO_ROOT / "toolkit"
TEMPLATE_FILENAME = "template.html.j2"


def _chart_lib() -> Markup:
    """The vendored Chart.js source, ready to emit verbatim inside the
    page skeleton's library <script>. This is our pinned, checksummed
    file (doctor verifies it), not content — hence Markup.
    chart_lib_inline_text (charts.py) is the single function that reads
    and prepares this text — it also strips the bundle's trailing
    sourceMappingURL comment and re-checks the inline-safety invariant
    (script element text must not be able to close the element or open
    an HTML comment) — and lint.py's script-body allowlist calls the
    same function, so the two can never drift on what "the inlined
    library" is."""
    try:
        lib = chart_lib_inline_text(CHARTJS_BUNDLE)
    except OSError as e:
        raise ContentError(
            f"cannot read vendored Chart.js at {CHARTJS_BUNDLE}: {e} "
            "(run 'jimemo doctor')"
        ) from e
    return Markup(lib)


def _charts_context(
    manifest: Dict[str, Any], content: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """One entry per manifest chart declaration, each carrying the full
    breakout-safe init-script body the chart macro embeds verbatim
    (Markup-wrapped: serialize_chart_config already u003c-escaped every
    "<" in the config, and autoescaping the body would corrupt it).
    chart_init_js builds the exact bytes; lint.py recognizes exactly
    that shape and rejects every other inline script on a chart page."""
    charts: List[Dict[str, Any]] = []
    for decl in manifest["charts"]:
        data_slot = decl["data_slot"]
        if data_slot not in content:
            raise ContentError(
                f"chart {decl['id']!r} reads data slot {data_slot!r}, "
                "but the content file provides no value for it"
            )
        try:
            config = build_chart_config(decl, content[data_slot])
        except ContentError as e:
            raise ContentError(
                f"chart {decl['id']!r} (data slot {data_slot!r}): {e}"
            ) from e
        charts.append({
            "id": decl["id"],
            "type": decl["type"],
            # Jinja's |default filter only fires on Undefined, not None,
            # so a missing/empty title must fall back to the chart id
            # here rather than relying on the template's default(c.id).
            "title": decl.get("title") or decl["id"],
            "init_js": Markup(
                chart_init_js(decl["id"], serialize_chart_config(config))
            ),
        })
    return charts


FIGURE_PLACEHOLDER = "<p>[[DIAGRAM:{name}]]</p>"

# The wrapper every spliced figure gets. `contain:paint` is the structural
# half of the sanitizer's "nothing leaves the figure" rule: `style` is kept
# on SVG elements (theming needs it), so a figure could declare
# `position:fixed;width:100vw;height:100vh` and paint over the whole page.
# Paint containment makes the <figure> the containing block for fixed and
# absolute descendants and clips their painting to its box — measured in
# Chromium: the same SVG goes from covering the viewport to being confined
# to the figure. Inline rather than in toolkit CSS on purpose: a stylesheet
# change would alter every page, and pages without figures must stay
# byte-identical.
FIGURE_OPEN = '<figure class="jm-figure" style="contain:paint">'

# Detail warning lines per figure before the rest is summarized in one
# line: a hostile figure with thousands of distinct unknown element names
# must not fill the author's terminal.
FIGURE_DROP_WARNINGS_MAX = 20


class _IdCollector(HTMLParser):
    """Every element id in a page — the first ``id`` attribute of each
    parsed start tag (text that merely looks like ``id="x"``, and script bodies, are
    not attributes and are not collected)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name != "id":
                continue
            # A browser keeps an element's FIRST id attribute and drops the
            # rest (sanitize_html keeps them all, so they reach the page);
            # _SVGSanitizer does the same for figures. An empty first id is
            # still the element's id, and matches nothing. This trusts
            # html.parser to split attributes as a browser does, which holds
            # from jimemo.PYTHON_FLOOR on (measured on 3.13.6, jimemo#1gs5).
            if value:
                self.ids.add(value)
            return

    handle_startendtag = handle_starttag


def _page_ids(html: str, prefix: str = "--figure") -> set:
    collector = _IdCollector()
    try:
        # A browser's HTML tokenizer turns U+0000 into U+FFFD; html.parser
        # passes it through. Normalize before the parse, exactly as
        # sanitize_svg_with_report does for a figure, so an anchor
        # id="g\x00" and a figure id="g\ufffd" compare equal here as they
        # do in the page. Only the collector's view changes: the rendered
        # page keeps whatever sanitize_html produced.
        collector.feed(html.replace("\x00", "\ufffd"))
        collector.close()
    except Exception as e:  # noqa: BLE001 - older html.parser raises assorted types
        # Fail closed: without the page's ids the collision check below
        # would silently not run.
        raise ContentError(
            f"{prefix}: could not read the rendered page's ids: {e}"
        ) from e
    return collector.ids


def _figure_drop_warnings(
    name: str, drops: List[SvgDrop], kind: str = "figure"
) -> List[str]:
    """The stderr warning lines for one figure's sanitizer drops: one per
    distinct drop, at most FIGURE_DROP_WARNINGS_MAX, then one summary line
    if more remain. The figure NAME goes through the same display filter as
    the dropped names (sanitize._svg_drop_label): it is CLI input an agent
    writes, and it shares the line with them. No dropped VALUE appears —
    the report does not carry one. `kind` is the line's first word:
    ``figure`` for --figure, ``image`` for an SVG markdown image."""
    label = _svg_drop_label(name)
    lines = [
        f"{kind} {label}: dropped {d.kind} {d.name} ({d.reason})"
        for d in drops[:FIGURE_DROP_WARNINGS_MAX]
    ]
    hidden = len(drops) - FIGURE_DROP_WARNINGS_MAX
    if hidden > 0:
        noun = "drop" if hidden == 1 else "drops"
        lines.append(f"{kind} {label}: {hidden} more distinct {noun} not shown")
    return lines


def _sanitize_svg_sources(
    sources: Dict[Any, str],
    page_ids: set,
    names: Dict[Any, str],
    subject: str,
    kind: str,
) -> Tuple[Dict[Any, str], List[str]]:
    """Sanitize every raw SVG in `sources` (key -> untrusted SVG text) and
    check its ids; returns ``(key -> sanitized markup, warning lines)``.
    Shared by --figure (_splice_figures) and SVG markdown images
    (_splice_svg_images), so both get one set of rules.

    `names[key]` is the RAW name shown for a source (a figure NAME, an
    image's src); every message shows it through _svg_drop_label, as
    ``{subject} {label}`` (``--figure FLOW``, ``image d.svg``). `kind`
    starts each drop-warning line (_figure_drop_warnings).

    Raises ContentError, before the caller changes anything: when a source
    is not acceptable SVG (sanitize_svg_with_report's ValueError, named);
    when a source defines an id in `page_ids` — inline SVG shares the
    page's one id namespace, so a gradient or a chart canvas id would
    resolve to the wrong element; and when two DIFFERENT keys define the
    same id. One key's repeated ids are its own and allowed."""
    sanitized: Dict[Any, str] = {}
    id_owner: Dict[str, Any] = {}
    warnings: List[str] = []
    for key, svg_text in sources.items():
        label = _svg_drop_label(names[key])
        try:
            svg, ids, drops = sanitize_svg_with_report(svg_text)
        except ValueError as e:
            raise ContentError(f"{subject} {label}: {e}") from e
        warnings.extend(_figure_drop_warnings(names[key], drops, kind))
        for svg_id in ids:
            if svg_id in page_ids:
                raise ContentError(
                    f"{subject} {label} defines id={svg_id!r}, which the page "
                    "already uses (a heading anchor, a chart, or an SVG "
                    "image); inline SVG shares the page's one id namespace — "
                    f"give the {kind}'s ids a distinct prefix"
                )
            owner = id_owner.setdefault(svg_id, key)
            if owner != key:
                raise ContentError(
                    f"{subject} {_svg_drop_label(names[owner])} and "
                    f"{subject} {label} both define id={svg_id!r}; "
                    "inline SVG shares the page's one id namespace, so "
                    f"url(#{_svg_drop_label(svg_id)}) would resolve to the "
                    f"wrong {kind} — give each {kind}'s ids a distinct prefix"
                )
        sanitized[key] = svg
    return sanitized, warnings


def _splice_figures(html: str, figures: Dict[str, str]) -> Tuple[str, List[str]]:
    """`html` with every ``<p>[[DIAGRAM:NAME]]</p>`` placeholder
    paragraph replaced by FIGURE_OPEN + the sanitized SVG for NAME +
    ``</figure>`` (`jimemo render --figure NAME=file.svg`;
    docs/diagrams.md). `figures` maps NAME to RAW, untrusted SVG text.

    Runs on the rendered page, so markdown sanitization has already
    happened — the placeholder is a paragraph sanitize_html let through
    as plain text — and BEFORE lint_html, which still judges the final
    page as the second guard. The SVG is rebuilt by sanitize_svg first;
    nothing raw is ever spliced. Plain ``str.replace`` on an exact
    string: no regex over the page.

    Raises ContentError, always before any replacement is made, when
    a figure is not acceptable SVG (named), when a NAME has no
    placeholder in the page — never a silent no-op — and when two
    DIFFERENT figures define the same ``id``, or a figure defines an id
    the page already uses: inline <svg> roots share the page's single id
    namespace, so figure B's ``url(#grad)`` would resolve to figure A's
    gradient and render wrong without any error — and a figure element
    that takes a chart canvas's id comes first in the document, so the
    chart's ``getElementById`` finds the SVG element and the chart never
    draws. One figure spliced at several placeholders repeats identical
    definitions, which resolve identically; that is allowed.

    Returns ``(html, warnings)``: `warnings` holds one line per distinct
    element or attribute the sanitizer dropped (_figure_drop_warnings), for
    render_page to print — the drops are silent otherwise, and a refused
    style shows up only as a wrongly painted shape.

    Every message here names a figure by its DISPLAY LABEL
    (sanitize._svg_drop_label), as the warnings do: a NAME is written by
    whoever ran the tool and an id comes from the untrusted figure file, and
    both land on a terminal. An id is ALSO printed with ``!r``, which is
    exact and escapes what a terminal would act on; the ``url(#…)`` form
    beside it is the label, because that one is read as the CSS it shows."""
    sanitized, warnings = _sanitize_svg_sources(
        figures,
        _page_ids(html),
        {name: name for name in figures},
        subject="--figure",
        kind="figure",
    )

    # Every placeholder is looked up in the page as rendered, before any
    # figure lands, so text inside one figure can never stand in for
    # another figure's placeholder.
    for name in sanitized:
        if FIGURE_PLACEHOLDER.format(name=name) in html:
            continue
        # The lookups above use the raw NAME — the page holds the real
        # placeholder, not a label — and only the messages below show it.
        label = _svg_drop_label(name)
        if f"[[DIAGRAM:{name}]]" in html:
            # The text is there, but not as the exact bare paragraph the
            # splice replaces. Say so: the usual cause is invisible in
            # the source (a trailing space renders as "…]] </p>").
            raise ContentError(
                f"--figure {label}: [[DIAGRAM:{label}]] is in the rendered "
                "page, but not as a paragraph of its own — remove any "
                "other text or trailing spaces on its line, and do not "
                "put it in a heading or a list item (see docs/diagrams.md)"
            )
        raise ContentError(
            f"--figure {label}: placeholder [[DIAGRAM:{label}]] not found "
            "in the rendered page — it must be a paragraph of its own "
            "in a markdown slot (see docs/diagrams.md)"
        )
    for name, svg in sanitized.items():
        html = html.replace(
            FIGURE_PLACEHOLDER.format(name=name),
            FIGURE_OPEN + svg + "</figure>",
        )
    return html, warnings


# --- SVG markdown images (jimemo#fqfq) ---------------------------------------
#
# `![alt](diagram.svg)` renders as the SVG itself, rebuilt by the same
# sanitizer --figure uses, inline — not as a data:image/svg+xml URI, which
# lint refuses and through which page tokens (var(--jm-*)) could not reach
# the drawing. docs/diagrams.md, "Route 2", is the user-facing statement.

# The wrapper replacing the <img>. A <span>, not a <figure>: markdown puts an
# image inside <p>, where a <figure> would make a browser close the
# paragraph early. display:block sizes a width:100% SVG against the column;
# contain:paint is FIGURE_OPEN's containment (a kept `style` may ask for
# position:fixed). Inline, not toolkit CSS, so pages without SVG images are
# byte-identical.
SVG_IMAGE_STYLE = "display:block;contain:paint"

# The only attributes an admitted <img> may carry: what sanitize_html emits
# for a markdown image. Anything else is left for inline_images and lint to
# refuse, rather than silently dropped by the replacement.
_SVG_IMAGE_ATTRS = frozenset({"src", "alt", "title"})

# While any of these is open, an <img> the parser reports is NOT one a
# browser would build, or not one it would build as HTML: raw text and
# RCDATA (whatever this Python's html.parser does with them — at the 3.13.6
# floor it treats only script/style as raw text), noscript (raw text when
# scripting is on), template (inert contents), and svg/math (foreign
# content). Such a tag is left alone; the later steps refuse a .svg in it.
_SVG_IMAGE_SKIP_CONTEXTS = frozenset({
    "script", "style", "xmp", "iframe", "noembed", "noframes", "textarea",
    "title", "noscript", "template", "svg", "math",
})

# Foreign elements: a browser honours their self-closing slash, so
# <svg/> opens nothing. Every other skip-context name ignores the slash
# in a browser (it opens the element and its text mode) and counts as open.
_SVG_IMAGE_FOREIGN = frozenset({"svg", "math"})


class _ImgTagFinder(HTMLParser):
    """Every <img> start tag in a page, as ``(start, end, attrs)`` source
    spans, outside the contexts in _SVG_IMAGE_SKIP_CONTEXTS. Comments and
    declarations are not tags and never reported."""

    def __init__(self, page: str) -> None:
        super().__init__(convert_charrefs=True)
        self._page = page
        # getpos() is (line, column); html.parser counts lines by "\n"
        # only, so a line's start is one past each "\n" in the page.
        self._line_starts = [0]
        index = page.find("\n")
        while index != -1:
            self._line_starts.append(index + 1)
            index = page.find("\n", index + 1)
        self._open = {}  # skip-context name -> open count
        self._plaintext = False
        # (start, end, attrs, raw tag text) per <img> outside skip contexts.
        self.tags: List[Tuple[int, int, List[Tuple[str, Optional[str]]], str]] = []

    def _skipping(self) -> bool:
        return self._plaintext or any(self._open.values())

    def _found(self, tag, attrs) -> None:
        if tag == "img" and not self._skipping():
            line, column = self.getpos()
            start = self._line_starts[line - 1] + column
            raw = self.get_starttag_text()
            self.tags.append((start, start + len(raw), attrs, raw))

    def handle_starttag(self, tag, attrs):
        self._found(tag, attrs)
        if tag == "plaintext":
            self._plaintext = True
        elif tag in _SVG_IMAGE_SKIP_CONTEXTS:
            self._open[tag] = self._open.get(tag, 0) + 1

    def handle_startendtag(self, tag, attrs):
        self._found(tag, attrs)
        if tag == "plaintext":
            self._plaintext = True
        elif tag in _SVG_IMAGE_SKIP_CONTEXTS and tag not in _SVG_IMAGE_FOREIGN:
            self._open[tag] = self._open.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if self._open.get(tag):
            self._open[tag] -= 1


def _svg_image_src(attrs: List[Tuple[str, Optional[str]]]) -> Optional[str]:
    """The `src` of an <img> the splice may replace, or None to leave it
    alone: attribute names within _SVG_IMAGE_ATTRS, none repeated (a
    second src must never be discarded unseen — lint checks every one),
    and a src that is a local path ending in .svg."""
    names = [name for name, _ in attrs]
    if len(set(names)) != len(names) or not set(names) <= _SVG_IMAGE_ATTRS:
        return None
    src = dict(attrs).get("src") or ""
    # The extension first: it is the cheap test, and it keeps every raster
    # <img> away from the URL parsing below, which can raise on a value
    # inline_images reads differently (it sees the raw attribute text).
    if not src.lower().endswith(".svg"):
        return None
    if src.startswith("#") or src.lower().startswith("data:"):
        return None
    try:
        if _is_remote(src):
            return None
    except ValueError:
        # urlsplit refuses it (an unmatched "[" after "//"): not a path
        # this step will read. inline_images and lint judge it.
        return None
    return src


def _splice_svg_images(
    html: str, base_dir: Path, svg_sources: Optional[List[Path]] = None
) -> Tuple[str, List[str]]:
    """`html` with every qualifying ``<img src="X.svg">`` replaced by a
    wrapper <span> holding the sanitized SVG from the local file X.svg
    (resolved against `base_dir`, the content file's directory). Returns
    ``(html, warnings)``; `warnings` are the sanitizer's drop lines,
    ``image X.svg: dropped …``. When `svg_sources` is a list, the resolved
    path of every SVG read is appended to it (the CLI refuses to write its
    output over one).

    Runs after the template render (markdown already sanitized) and before
    inline_images and lint. A page with no ``<img`` is returned as the same
    string without a parse; a page the parser cannot read, or with no
    qualifying tag, is returned unchanged — every .svg left in it reaches
    inline_images and lint, which refuse it as before. So no page that
    rendered before this step existed can start failing here.

    Raises ContentError for a qualifying tag whose file is unsafe (absolute,
    escapes `base_dir`), missing, unreadable or not one acceptable <svg>,
    and for id collisions (_sanitize_svg_sources)."""
    if "<img" not in html.lower():
        return html, []
    finder = _ImgTagFinder(html)
    try:
        finder.feed(html)
        finder.close()
    except Exception:  # noqa: BLE001 - fall through to the existing refusals
        return html, []

    spans: List[Tuple[int, int, Path, Dict[str, Optional[str]]]] = []
    names: Dict[Path, str] = {}
    rejected: List[str] = []
    missing: List[str] = []
    for start, end, attrs, raw in finder.tags:
        src = _svg_image_src(attrs)
        if src is None:
            continue
        if html[start:end] != raw:
            # An offset bug, never expected: refuse rather than splice
            # over the wrong bytes.
            raise ContentError(
                f"image {_svg_drop_label(src)}: could not locate its <img> "
                "tag in the rendered page"
            )
        path, reason = resolve_local_image(src, base_dir)
        if path is None:
            rejected.append(f"{src} ({reason})")
            continue
        if not is_local_file(path):
            missing.append(src)
            continue
        names.setdefault(path, src)
        spans.append((start, end, path, dict(attrs)))
    if rejected:
        raise ContentError(
            "unsafe local image path(s) in image attributes: " + "; ".join(rejected)
        )
    if missing:
        raise ContentError("missing local image(s): " + ", ".join(missing))
    if not spans:
        return html, []

    sources: Dict[Path, str] = {}
    for path, src in names.items():
        try:
            sources[path] = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as e:
            raise ContentError(
                f"image {_svg_drop_label(src)}: cannot read the SVG file: "
                f"{e.__class__.__name__}"
            ) from e
    sanitized, warnings = _sanitize_svg_sources(
        sources, _page_ids(html, "SVG images"), names, subject="image", kind="image"
    )
    if svg_sources is not None:
        svg_sources.extend(names)

    parts: List[str] = []
    cursor = 0
    for start, end, path, attrs in spans:
        parts.append(html[cursor:start])
        parts.append(_svg_image_wrapper(attrs, sanitized[path]))
        cursor = end
    parts.append(html[cursor:])
    return "".join(parts), warnings


def _svg_image_wrapper(attrs: Dict[str, Optional[str]], svg: str) -> str:
    """The <span> that replaces an admitted <img>: role="img" +
    aria-label from a non-empty alt, aria-hidden="true" for an empty or
    missing or blank one (decorative), the title when present, and
    SVG_IMAGE_STYLE. Values are the parsed attribute values, escaped
    once."""
    alt = attrs.get("alt") or ""
    parts = ["<span"]
    if alt.strip():
        parts.append(f' role="img" aria-label="{escape(alt, quote=True)}"')
    else:
        parts.append(' aria-hidden="true"')
    title = attrs.get("title")
    if title:
        parts.append(f' title="{escape(title, quote=True)}"')
    parts.append(f' style="{SVG_IMAGE_STYLE}">')
    return "".join(parts) + svg + "</span>"


def render_page(
    template_dir: Path,
    content: Dict[str, Any],
    theme: Optional[str] = None,
    *,
    base_dir: Optional[Path] = None,
    figures: Optional[Dict[str, str]] = None,
    svg_sources: Optional[List[Path]] = None,
) -> str:
    """Full HTML string (assembled + inlined) for `content` rendered
    through the template in `template_dir`. `base_dir` is the directory
    local <img> paths in content are resolved against (the content
    file's parent); it defaults to the current working directory when
    omitted, which is only correct if content carries no local images.
    `figures` maps a ``[[DIAGRAM:NAME]]`` placeholder NAME to raw SVG
    text to splice in its place, sanitized (see _splice_figures); None
    or empty runs no figure code at all, so such pages are byte-for-byte
    what they were before the parameter existed. Whatever the sanitizer
    drops from a figure is reported as ``warning: figure NAME: …`` lines
    on stderr, with the other warnings.

    A markdown image of a local ``.svg`` file is replaced by that SVG,
    sanitized, inline (_splice_svg_images; docs/diagrams.md). When
    `svg_sources` is a list, the resolved path of every such file is
    appended to it, so a caller can refuse to write output over one.

    Raises ContentError if lint finds a hard error (any resource
    reference outside lint's self-contained allowlist, script tags where
    the manifest declares no charts, any <script src>, or a chart page
    whose inline scripts are not exactly the ones this renderer emitted
    for it — forged, extra, duplicated, or missing bodies all) — callers
    must not write output in that case — and for chart data that is
    missing or does not fit the {labels, series} contract (see charts.py).
    """
    template_dir = Path(template_dir)
    manifest = load_manifest(template_dir)

    env = Environment(
        loader=FileSystemLoader([str(template_dir), str(TOOLKIT_DIR)]),
        autoescape=True,
        undefined=StrictUndefined,
    )
    try:
        template = env.get_template(TEMPLATE_FILENAME)
    except TemplateError as e:
        # Missing template.html.j2, or one with a syntax error: surface
        # as the domain error the CLI already prints cleanly, naming the
        # template so the author knows what to fix.
        raise ContentError(
            f"template {TEMPLATE_FILENAME!r} in {template_dir} could not "
            f"be loaded: {e}"
        ) from e

    styles = Markup("<style>\n" + assemble_css(manifest, theme) + "\n</style>")

    context: Dict[str, Any] = dict(content)
    context["manifest"] = manifest
    context["styles"] = styles
    context["theme"] = theme

    # Chartless manifests inject NOTHING here — their rendered output
    # is byte-identical to the chartless form (the goldens pin this). The
    # charts/chart_lib slot-name collision is validated authoritatively
    # in load_manifest (manifest.py), which this function has already
    # called above to obtain `manifest` — so it cannot reach this point
    # uncaught, and re-checking it here would be dead code.
    allowed_scripts: Optional[List[str]] = None
    if manifest["charts"]:
        context["chart_lib"] = _chart_lib()
        context["charts"] = _charts_context(manifest, content)
        # The renderer is the source of truth for what inline script a
        # chart page may carry: exactly the library body and each
        # chart's init body — the very values injected into the context
        # above. lint_html gets this list and requires the page's
        # inline scripts to EQUAL it, so a template cannot add, drop,
        # duplicate, or forge a script body — not even one that apes
        # the init shape for a declared id with different config bytes,
        # which lint's structural fallback alone could not tell apart.
        allowed_scripts = [str(context["chart_lib"])] + [
            str(chart["init_js"]) for chart in context["charts"]
        ]

    try:
        html = template.render(**context)
    except UndefinedError as e:
        # StrictUndefined raises on any unknown name; surface it as the
        # domain error the CLI already prints cleanly (no traceback).
        raise ContentError(f"template referenced an undefined value: {e}") from e
    except TemplateError as e:
        raise ContentError(
            f"template {TEMPLATE_FILENAME!r} in {template_dir} failed to "
            f"render: {e}"
        ) from e

    resolved_base = (Path(base_dir) if base_dir else Path.cwd()).resolve()
    local_svgs: List[Path] = []
    html, svg_image_warnings = _splice_svg_images(html, resolved_base, local_svgs)
    if svg_sources is not None:
        svg_sources.extend(local_svgs)

    html, img_warnings = inline_images(html, resolved_base)

    # Defined on both paths: a page without --figure runs no figure code
    # and prints exactly the warnings it printed before.
    figure_warnings: List[str] = []
    if figures:
        html, figure_warnings = _splice_figures(html, figures)

    errors, warnings = lint_html(html, manifest, allowed_scripts=allowed_scripts)
    if errors:
        message = "; ".join(errors)
        # Lint judges the whole page and cannot say which part an error
        # came from; point at the inputs that are new to it.
        spliced = [
            label
            for label, present in (("--figure SVG", figures), ("an SVG image", local_svgs))
            if present
        ]
        if spliced:
            message += (
                f" (this page includes {' and '.join(spliced)}: see 'What "
                "the sanitizer removes' in docs/diagrams.md)"
            )
        raise ContentError(message)

    for w in [*svg_image_warnings, *img_warnings, *figure_warnings, *warnings]:
        print(f"warning: {w}", file=sys.stderr)

    return html


def write_output(html: str, out_path: Path) -> None:
    out_path = Path(out_path)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
    except OSError as e:
        raise ContentError(f"cannot write output file {out_path}: {e}") from e
