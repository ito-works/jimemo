"""Tests for SVG files used as markdown images (jimemo#fqfq): a local
``![alt](d.svg)`` is replaced by the sanitized SVG, inline
(jimemo.render._splice_svg_images).

End-to-end tests go through the real briefing template and the real
markdown path, so the <img> they start from is exactly what
python-markdown + sanitize_html emit. Tests of tags markdown can never
produce (duplicate attributes, comments, raw-text contexts) use a small
template of their own or call the splice directly.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.cli import main
from jimemo.content import load_content
from jimemo.errors import ContentError
from jimemo.lint import lint_html
from jimemo.manifest import load_manifest
from jimemo import render
from jimemo.render import SVG_IMAGE_STYLE, _splice_svg_images, render_page
from markupsafe import Markup  # vendored; importing jimemo.render adds it

BRIEFING_DIR = Path(__file__).resolve().parents[1] / "templates" / "briefing"

GOOD_SVG = (
    '<svg viewBox="0 0 760 120" role="img" aria-label="A flow." '
    'style="width:100%;height:auto;font-family:var(--jm-font-ui)">'
    '<rect id="d-box" x="10" y="10" width="200" height="60" '
    'style="fill:var(--jm-accent);stroke:var(--jm-border)"/>'
    '<text x="20" y="40" style="fill:var(--jm-text)">Flow</text>'
    "</svg>"
)

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

# A harmless sibling every payload test must keep.
SIBLING = '<rect id="kept" width="4" height="4"/>'


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # Keeps a personal ~/.jimemo theme from shadowing the repo's.
    monkeypatch.setenv("HOME", str(tmp_path / "isolated-home"))


def _content(tmp_path, body: str, svgs=None):
    """Briefing content loaded through the real markdown path, with the
    given SVG files written beside it."""
    for name, text in (svgs or {}).items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    src = tmp_path / "content.md"
    src.write_text('---\ntitle: "T"\ndate: "29 September 2026"\n---\n' + body)
    return load_content(src, load_manifest(BRIEFING_DIR))


def _render(tmp_path, body: str, svgs=None, **kw) -> str:
    return render_page(
        BRIEFING_DIR, _content(tmp_path, body, svgs), base_dir=tmp_path, **kw
    )


def _span(html: str) -> str:
    """The first SVG-image wrapper span in `html`, whole."""
    start = html.index(f'style="{SVG_IMAGE_STYLE}"><svg')
    start = html.rindex("<span", 0, start)
    return html[start:html.index("</svg></span>", start) + len("</svg></span>")]


# --- the admitted form ------------------------------------------------------


def test_markdown_svg_image_renders_inline_and_passes_lint(tmp_path):
    html = _render(
        tmp_path, "Before.\n\n![A flow diagram](d.svg)\n\nAFTER marker.\n",
        {"d.svg": GOOD_SVG},
    )  # render_page raises on any lint error
    assert "<img" not in html
    span = _span(html)
    assert span.startswith(
        '<span role="img" aria-label="A flow diagram" '
        'style="display:block;contain:paint"><svg viewBox="0 0 760 120"'
    )
    assert html.index("Before.") < html.index(span) < html.index("AFTER marker.")
    errors, _ = lint_html(html, load_manifest(BRIEFING_DIR))
    assert errors == []


def test_rendered_page_passes_jimemo_check(tmp_path, capsys):
    out = tmp_path / "out.html"
    _content(tmp_path, "![d](d.svg)\n", {"d.svg": GOOD_SVG})
    assert main(["render", "briefing", str(tmp_path / "content.md"),
                 "-o", str(out)]) == 0
    assert main(["check", str(out)]) == 0
    assert "aria-label=\"d\"" in out.read_text()


def test_theme_tokens_survive_as_inline_markup(tmp_path):
    html = _render(tmp_path, "![d](d.svg)\n", {"d.svg": GOOD_SVG})
    span = _span(html)
    assert 'style="fill:var(--jm-accent);stroke:var(--jm-border)"' in span
    assert 'style="fill:var(--jm-text)"' in span
    assert "font-family:var(--jm-font-ui)" in span


def test_alt_and_title_are_escaped_onto_the_wrapper(tmp_path):
    html = _render(tmp_path, '![A "q" & b](d.svg "a <tip>")\n', {"d.svg": GOOD_SVG})
    assert _span(html).startswith(
        '<span role="img" aria-label="A &quot;q&quot; &amp; b" '
        'title="a &lt;tip&gt;" style='
    )


def test_empty_alt_marks_the_image_decorative(tmp_path):
    html = _render(tmp_path, '![](d.svg "tip")\n', {"d.svg": GOOD_SVG})
    assert _span(html).startswith('<span aria-hidden="true" title="tip" style=')


def test_image_inside_a_sentence_stays_in_its_paragraph(tmp_path):
    html = _render(tmp_path, "Text ![d](d.svg) inline.\n", {"d.svg": GOOD_SVG})
    assert "<p>Text <span role=\"img\"" in html
    assert "</svg></span> inline.</p>" in html


def test_uppercase_extension_is_admitted(tmp_path):
    html = _render(tmp_path, "![d](D.SVG)\n", {"D.SVG": GOOD_SVG})
    assert "<img" not in html and "<svg" in html


def test_drop_warnings_name_the_image(tmp_path, capsys):
    evil = '<svg viewBox="0 0 9 9"><script>x()</script>' + SIBLING + "</svg>"
    _render(tmp_path, "![d](d.svg)\n", {"d.svg": evil})
    err = capsys.readouterr().err
    assert "warning: image d.svg: dropped element script (not allowlisted)" in err


# --- one test per vector: payload gone, sibling kept ------------------------

VECTORS = {
    "script": '<script>evil()</script>',
    "foreignObject": '<foreignObject><div onclick="evil()">evil</div></foreignObject>',
    "on-handler": '<rect onclick="evil()" width="1" height="1"/>',
    "external-href": '<use href="https://evil.example/x.svg#a"/>',
    "external-xlink-href": '<use xlink:href="https://evil.example/x.svg#a"/>',
    "use-other-document": '<use href="evil.svg#a"/>',
    "image-remote": '<image href="https://evil.example/p.png"/>',
    "image-local-file": '<image href="evil.png"/>',
    "image-svg-data-uri": '<image href="data:image/svg+xml,&lt;svg onload=evil()&gt;"/>',
    "style-svg-data-uri": '<rect style="fill:url(data:image/svg+xml,evil)" width="1" height="1"/>',
    "paint-remote-url": '<rect fill="url(https://evil.example/p#g)" width="1" height="1"/>',
}


@pytest.mark.parametrize("vector", sorted(VECTORS))
def test_vector_is_stripped_through_the_image_path(tmp_path, vector):
    svg = '<svg viewBox="0 0 9 9" onload="evil()">' + VECTORS[vector] + SIBLING + "</svg>"
    html = _render(tmp_path, "![d](d.svg)\n", {"d.svg": svg})
    span = _span(html)
    for gone in ("evil", "onload", "onclick", "script", "foreignObject",
                 "<image", "href", "data:"):
        assert gone not in span, (vector, gone)
    assert '<rect id="kept" width="4" height="4" />' in span


def test_svg_data_uri_on_img_src_is_still_a_lint_error():
    page = '<!doctype html><html><body><img src="data:image/svg+xml,%3Csvg/%3E"></body></html>'
    errors, _ = lint_html(page, {"charts": []})
    assert errors


# --- ids share the page's namespace -----------------------------------------


def test_one_file_twice_may_repeat_its_own_ids(tmp_path):
    html = _render(tmp_path, "![a](d.svg)\n\n![b](./d.svg)\n", {"d.svg": GOOD_SVG})
    assert html.count('id="d-box"') == 2


def test_two_files_defining_one_id_are_refused(tmp_path):
    with pytest.raises(ContentError, match=r"image a\.svg and image b\.svg both define id='d-box'"):
        _render(tmp_path, "![a](a.svg)\n\n![b](b.svg)\n",
                {"a.svg": GOOD_SVG, "b.svg": GOOD_SVG})


def test_image_id_already_used_by_the_page_is_refused(tmp_path):
    svg = '<svg><rect id="methods" width="1" height="1"/></svg>'
    with pytest.raises(ContentError, match="image d.svg defines id='methods', which the page already uses"):
        _render(tmp_path, '<a id="methods"></a>Methods.\n\n![d](d.svg)\n', {"d.svg": svg})


def test_same_file_as_figure_and_image_is_refused(tmp_path):
    with pytest.raises(ContentError, match=r"--figure F defines id='d-box', which the page already uses \(a heading anchor, a chart, or an SVG image\)"):
        _render(tmp_path, "![d](d.svg)\n\n[[DIAGRAM:F]]\n", {"d.svg": GOOD_SVG},
                figures={"F": GOOD_SVG})


# --- files: path safety and bad input ---------------------------------------


@pytest.mark.parametrize("src, message", [
    ("/etc/d.svg", "absolute path"),
    ("../outside.svg", "escapes the content file's directory"),
    ("missing.svg", "missing local image"),
    ("dir.svg", "missing local image"),
    ("x" * 300 + ".svg", "missing local image"),  # ENAMETOOLONG: no traceback
])
def test_unsafe_or_missing_svg_is_a_content_error(tmp_path, src, message):
    content_dir = tmp_path / "c"
    content_dir.mkdir()
    (tmp_path / "outside.svg").write_text(GOOD_SVG)
    (content_dir / "dir.svg").mkdir()
    with pytest.raises(ContentError, match=message):
        _render(content_dir, f"![d]({src})\n")


def test_symlink_escaping_the_content_directory_is_refused(tmp_path):
    content_dir = tmp_path / "c"
    content_dir.mkdir()
    (tmp_path / "secret.svg").write_text(GOOD_SVG)
    os.symlink(tmp_path / "secret.svg", content_dir / "d.svg")
    with pytest.raises(ContentError, match="escapes the content file's directory"):
        _render(content_dir, "![d](d.svg)\n")


def test_file_that_is_not_one_svg_is_named(tmp_path):
    with pytest.raises(ContentError, match=r"image d\.svg: .*<svg>"):
        _render(tmp_path, "![d](d.svg)\n", {"d.svg": "<p>not an svg</p>"})


def test_non_utf8_file_is_named(tmp_path):
    (tmp_path / "d.svg").write_bytes(b"<svg>\xff\xfe</svg>")
    with pytest.raises(ContentError, match=r"image d\.svg: cannot read"):
        _render(tmp_path, "![d](d.svg)\n")


# --- what is NOT admitted (template-level: markdown cannot produce these) ---

SPLICE_MANIFEST = """\
{"name": "t", "version": 1, "title": "T",
 "slots": {"body": {"type": "markdown", "required": true}},
 "components": [], "charts": []}
"""


def _template_render(tmp_path, markup: str, **kw) -> str:
    template_dir = tmp_path / "tpl"
    template_dir.mkdir()
    (template_dir / "manifest.json").write_text(SPLICE_MANIFEST)
    (template_dir / "template.html.j2").write_text(
        '{% extends "page.html.j2" %}{% block title %}T{% endblock %}'
        "{% block content %}" + markup + "{{ body }}{% endblock %}"
    )
    (tmp_path / "d.svg").write_text(GOOD_SVG)
    return render_page(template_dir, {"body": Markup("<p>x</p>")},
                       base_dir=tmp_path, **kw)


@pytest.mark.parametrize("tag", [
    '<img src="d.svg" src="data:image/svg+xml,%3Csvg/%3E">',
    '<img src="data:image/svg+xml,%3Csvg/%3E" src="d.svg">',
    '<img src="d.svg" width="3">',
    '<img src="d.svg" alt="a" alt="b">',
])
def test_tag_that_does_not_qualify_is_left_and_refused(tmp_path, tag):
    assert _splice_svg_images(tag, tmp_path) == (tag, [])
    with pytest.raises(ContentError):
        _template_render(tmp_path, tag)


def test_entity_encoded_extension_is_admitted(tmp_path):
    html = _template_render(tmp_path, '<img src="d.sv&#103;" alt="x">')
    assert "<img" not in html and 'aria-label="x"' in html


@pytest.mark.parametrize("markup", [
    '<img srcset="d.svg 2x" src="d.svg">',
    '<picture><source srcset="d.svg"></picture>',
    '<video poster="d.svg"></video>',
])
def test_svg_stays_refused_in_srcset_source_and_poster(tmp_path, markup):
    with pytest.raises(ContentError, match="image type"):
        _template_render(tmp_path, markup)


@pytest.mark.parametrize("page", [
    "<!-- <img src=d.svg> --><p>x</p>",
    "<xmp><img src=d.svg></xmp>",
    "<noembed><img src=d.svg></noembed>",
    "<noframes><img src=d.svg></noframes>",
    "<noscript><img src=d.svg></noscript>",
    "<template><img src=d.svg></template>",
    "<textarea><img src=d.svg></textarea>",
    "<title/><img src=d.svg>",
    "<svg><img src=d.svg></svg>",
    "<plaintext><img src=d.svg>",
    "<iframe><img src=d.svg></iframe>",
    "<math><img src=d.svg></math>",
    "<noscript/><img src=d.svg>",
    '<img src="DATA:image/svg+xml,x.svg">',
    '<img src="http&colon;//[.png">',
    '<img src="http&colon;//[.svg">',
    "<p>no image at all</p>",
    '<img src="a.png" alt="raster">',
])
def test_img_outside_ordinary_html_is_left_byte_identical(tmp_path, page):
    (tmp_path / "d.svg").write_text(GOOD_SVG)
    out, warnings = _splice_svg_images(page, tmp_path)
    assert out is page and warnings == []


def test_self_closing_svg_and_stray_end_tag_do_not_suppress(tmp_path):
    (tmp_path / "d.svg").write_text(GOOD_SVG)
    out, _ = _splice_svg_images("<svg/></title><img src=d.svg>", tmp_path)
    assert out.startswith("<svg/></title><span aria-hidden")


def test_offsets_hold_across_line_ends_and_non_ascii(tmp_path):
    (tmp_path / "d.svg").write_text(GOOD_SVG)
    page = 'é\r\n<p>一\r<img src="d.svg">\n<img src="d.svg"></p>\n'
    out, _ = _splice_svg_images(page, tmp_path)
    assert out.startswith("é\r\n<p>一\r<span aria-hidden")
    assert out.count("<svg") == 2 and "<img" not in out
    assert out.endswith("</svg></span></p>\n")


def test_raster_page_renders_exactly_as_without_the_new_step(tmp_path, monkeypatch):
    (tmp_path / "p.png").write_bytes(TINY_PNG)
    content = _content(tmp_path, "Before.\n\n![p](p.png)\n\n## A heading\n")
    html = render_page(BRIEFING_DIR, content, base_dir=tmp_path)
    monkeypatch.setattr(render, "_splice_svg_images", lambda h, b, s=None: (h, []))
    assert render_page(BRIEFING_DIR, content, base_dir=tmp_path) == html
    assert "data:image/png;base64," in html and SVG_IMAGE_STYLE not in html


def test_malformed_url_is_refused_cleanly_not_a_traceback(tmp_path):
    # The decoded src is http://[.svg, which urlsplit refuses; the tag is
    # left for inline_images, which refuses the .svg as before.
    with pytest.raises(ContentError, match="image type"):
        _template_render(tmp_path, '<img src="http&colon;//[.svg">')


@pytest.mark.parametrize("src", ["http://[.svg", "http://[.png"])
def test_unparseable_url_is_a_content_error(tmp_path, src):
    # urlsplit raises on an unmatched "[" after "//"; the splice leaves the
    # tag alone and inline_images names it instead of a traceback.
    with pytest.raises(ContentError, match=r"not a valid URL"):
        _render(tmp_path, f"![d]({src})\n")


def test_blank_alt_is_decorative(tmp_path):
    html = _render(tmp_path, "![ ](d.svg)\n", {"d.svg": GOOD_SVG})
    assert _span(html).startswith('<span aria-hidden="true" style=')


# --- the CLI does not overwrite a source SVG --------------------------------


def _fake_pdf_seam(monkeypatch):
    calls = []
    monkeypatch.setattr("jimemo.pdf.find_browser", lambda configured=None, **kw: "/b")

    def fake_render_pdf(html_path, pdf_path, browser_, launcher=None):
        calls.append(pdf_path)
        Path(pdf_path).write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr("jimemo.pdf.render_pdf", fake_render_pdf)
    return calls


@pytest.mark.parametrize("link", [None, "hard", "sym"])
@pytest.mark.parametrize("mode", ["html", "pdf-only", "html+pdf"])
def test_render_refuses_to_write_over_an_svg_image(tmp_path, monkeypatch, capsys, link, mode):
    monkeypatch.setenv("JIMEMO_CONFIG", str(tmp_path / "absent.toml"))
    calls = _fake_pdf_seam(monkeypatch)
    _content(tmp_path, "![d](d.svg)\n", {"d.svg": GOOD_SVG})
    source = tmp_path / "d.svg"
    alias = source
    if link is not None:
        alias = tmp_path / ("alias.pdf" if mode != "html" else "alias.html")
        (os.link if link == "hard" else os.symlink)(source, alias)
    elif mode != "html":
        pytest.skip("a PDF target must end in .pdf; only a link can alias d.svg")
    out_html = tmp_path / "out.html"
    argv = ["render", "briefing", str(tmp_path / "content.md")]
    if mode == "html":
        argv += ["-o", str(alias)]
    elif mode == "pdf-only":
        argv += ["-o", str(alias)]
    else:
        argv += ["-o", str(out_html), "--pdf", str(alias)]

    assert main(argv) == 2

    assert "is an SVG image in the content; refusing to overwrite it" in capsys.readouterr().err
    assert source.read_text() == GOOD_SVG
    assert calls == [] and not out_html.exists()
