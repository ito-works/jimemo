"""The renderer-built tooltip (jimemo#7h7c): detection, emission, lint
recognition, and a briefing golden with tooltips.

Regenerate the golden with
    JIMEMO_UPDATE_GOLDENS=1 python3 -m pytest tests/test_tooltip.py
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._paths import REPO_ROOT
from jimemo.content import load_content
from jimemo.errors import ContentError
from jimemo.lint import lint_html, lint_standalone
from jimemo.manifest import load_manifest
from jimemo.render import render_page
from jimemo.tooltip import content_has_title, tooltip_css_text, tooltip_js_text
from markupsafe import Markup

BRIEFING = REPO_ROOT / "templates" / "briefing"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "tooltip" / "content.md"
GOLDENS = Path(__file__).resolve().parent / "goldens"
TOOLTIP_MARKER = "jimemo tooltip (jimemo#7h7c). Fixed"

JS = tooltip_js_text()


def _render(content_path):
    manifest = load_manifest(BRIEFING)
    content = load_content(content_path, manifest)
    return render_page(BRIEFING, content, base_dir=content_path.parent)


def _page(*scripts, extra=""):
    body = "".join(f"<script>{s}</script>" for s in scripts)
    return f"<html><body><p><span title=\"t\">x</span></p>{extra}{body}</body></html>"


# --- the toolkit text ---

def test_tooltip_js_is_inline_safe_and_uses_text_content():
    assert "</script" not in JS.lower() and "<!--" not in JS
    assert "innerHTML" not in JS and "outerHTML" not in JS
    assert "textContent" in JS
    assert 'removeAttribute("title")' in JS


def test_tooltip_css_uses_page_tokens_and_pre_line():
    css = tooltip_css_text()
    for token in ("--jm-surface", "--jm-text", "--jm-accent", "--jm-border", "--jm-font-ui"):
        assert f"var({token})" in css
    assert "white-space: pre-line" in css
    # A tall title is cut at the viewport rather than running off it.
    assert "max-height: calc(100vh - 8px)" in css and "overflow: hidden" in css
    assert "url(" not in css and "@import" not in css


def test_tooltip_js_text_refuses_a_script_breakout(tmp_path):
    bad = tmp_path / "tooltip.js"
    bad.write_text("var a = '</script><script>alert(1)';", encoding="utf-8")
    with pytest.raises(ContentError):
        tooltip_js_text(bad)


def test_tooltip_js_text_refuses_a_comment_opener(tmp_path):
    bad = tmp_path / "tooltip.js"
    bad.write_text("var a = '<!-- x';", encoding="utf-8")
    with pytest.raises(ContentError):
        tooltip_js_text(bad)


def test_tooltip_css_text_refuses_a_style_breakout(tmp_path):
    bad = tmp_path / "tooltip.css"
    bad.write_text("a{}</style><script>alert(1)</script>", encoding="utf-8")
    with pytest.raises(ContentError):
        tooltip_css_text(bad)


# --- detection ---

def test_content_has_title_finds_nested_markdown():
    content = {
        "title": "plain",
        "body": Markup("<p>no tips</p>"),
        "sections": [{"heading": "h", "body": Markup('<p><span title="t">x</span></p>')}],
    }
    assert content_has_title(content)


def test_content_has_title_ignores_text_and_empty_titles():
    content = {
        "title": '<span title="looks like markup">x</span>',  # text slot: plain str
        "body": Markup('<p><a href="#x" title="">x</a></p>'),
    }
    assert not content_has_title(content)


def test_content_has_title_survives_cyclic_and_shared_yaml(tmp_path):
    shared = [{"a": [1, 2]}]
    loop = []
    loop.append(loop)
    content = {"d": [shared] * 50, "e": loop, "body": Markup("<p>x</p>")}
    assert not content_has_title(content)
    content["e"].append(Markup('<b title="t">x</b>'))
    assert content_has_title(content)


def test_render_survives_a_self_referential_data_slot(tmp_path):
    template = tmp_path / "t"
    template.mkdir()
    (template / "manifest.json").write_text(
        '{"name": "t", "version": 1, "title": "t", "description": "t",'
        ' "slots": {"body": {"type": "markdown", "required": true},'
        ' "rows": {"type": "data"}}, "components": [], "charts": []}',
        encoding="utf-8",
    )
    (template / "template.html.j2").write_text(
        '{% extends "page.html.j2" %}{% block content %}{{ body }}{% endblock %}',
        encoding="utf-8",
    )
    src = tmp_path / "c.md"
    src.write_text(
        "---\nrows: &r\n  - x\n  - *r\n---\nA <span title=\"t\">tip</span>.\n",
        encoding="utf-8",
    )
    content = load_content(src, load_manifest(template))
    html = render_page(template, content, base_dir=tmp_path)
    assert html.count(TOOLTIP_MARKER) == 1


# --- emission ---

def test_title_free_content_renders_without_tooltip_and_matches_golden():
    sample = BRIEFING / "sample" / "content.md"
    html = _render(sample)
    assert "jm-tip" not in html and TOOLTIP_MARKER not in html
    assert html == (GOLDENS / "briefing.html").read_text(encoding="utf-8")


def test_title_in_content_emits_the_tooltip_once_and_passes_check():
    html = _render(FIXTURE)
    assert html.count(TOOLTIP_MARKER) == 1
    assert html.count("<script>") == 1
    assert html.count("jimemo tooltip (jimemo#7h7c): the box") == 1
    assert lint_standalone(html)[0] == []
    # The script sits after all content, just before </body>.
    assert html.index(TOOLTIP_MARKER) > html.index('title="Mod: Neural Overclock"')
    assert html.rstrip().endswith("</script>\n</body>\n</html>")


def test_briefing_tooltip_golden():
    html = _render(FIXTURE)
    golden = GOLDENS / "briefing-tooltip.html"
    if os.environ.get("JIMEMO_UPDATE_GOLDENS"):
        golden.write_text(html, encoding="utf-8")
    assert golden.is_file(), "run with JIMEMO_UPDATE_GOLDENS=1 to create it"
    assert html == golden.read_text(encoding="utf-8")


# --- lint ---

@pytest.mark.parametrize("charts", ([], ["sales"]))
def test_lint_html_accepts_the_tooltip_script(charts):
    errors, _ = lint_html(_page(JS), {"charts": charts})
    assert errors == []


def test_lint_exact_mode_accepts_the_tooltip_beside_chart_scripts():
    init = 'new Chart(document.getElementById("sales"), {});'
    html = _page(JS, init, extra='<canvas id="sales"></canvas>')
    errors, _ = lint_html(html, {"charts": ["sales"]}, allowed_scripts=[init])
    assert errors == []


def test_lint_standalone_accepts_the_tooltip_script_with_surrounding_whitespace():
    assert lint_standalone(_page("\n" + JS + "\n"))[0] == []


def test_page_with_title_and_no_script_is_fine():
    assert lint_html(_page(), {"charts": []})[0] == []


@pytest.mark.parametrize("edit", (
    lambda js: js.replace("textContent", "innerHTML", 1),
    lambda js: js + ";",
    lambda js: js.replace('"jm-tip"', '"jm-tip2"', 1),
))
def test_lint_rejects_a_modified_tooltip_script(edit):
    modified = edit(JS)
    assert modified.strip() != JS.strip()
    errors, _ = lint_html(_page(modified), {"charts": []})
    assert any("declares no charts" in e for e in errors)
    errors, _ = lint_standalone(_page(modified))
    assert any("unexpected inline" in e for e in errors)


@pytest.mark.parametrize("wrap", (
    lambda s: f"<script>{s}</script>",
    lambda s: f"<template><script>{s}</script></template>",
    lambda s: f"<noscript><script>{s}</script></noscript>",
))
def test_lint_rejects_a_second_tooltip_script(wrap):
    html = _page(JS, extra=wrap(JS))
    for errors in (lint_html(html, {"charts": []})[0], lint_standalone(html)[0]):
        assert any("duplicate tooltip" in e for e in errors), errors


def test_other_scripts_still_refused_on_a_tooltip_page():
    html = _page(JS, "alert(1)")
    assert any("declares no charts" in e for e in lint_html(html, {"charts": []})[0])
    assert any("unexpected inline" in e for e in lint_standalone(html)[0])


def test_tooltip_body_does_not_bless_a_src_script():
    html = f'<html><body><script src="x.js">{JS}</script></body></html>'
    errors, _ = lint_html(html, {"charts": []})
    assert errors == ['<script src="x.js"> is never allowed']


@pytest.mark.parametrize("attr", ("href", "xlink:href"))
@pytest.mark.parametrize("url", ("https://evil.example/x.js", "data:text/javascript,alert(1)"))
def test_tooltip_body_does_not_bless_an_svg_script_href(attr, url):
    # An SVG <script> fetches and runs href/xlink:href like src.
    html = (
        f'<html><body><svg><script {attr}="{url}">{JS}</script></svg>'
        "</body></html>"
    )
    for errors in (lint_html(html, {"charts": []})[0], lint_standalone(html)[0]):
        assert any(f"<script {attr}=" in e and "never allowed" in e for e in errors), errors
