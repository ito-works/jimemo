import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.content import _first_char, _render_markdown, load_content
from jimemo.errors import ContentError

MANIFEST = {
    "slots": {
        "title": {"type": "text", "required": True},
        "date": {"type": "text"},
        "body": {"type": "markdown", "required": True},
        "sections": {"type": "data", "items": {"heading": "text", "body": "markdown"}},
        "tags": {"type": "data"},
    }
}


def test_md_frontmatter_and_body(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: Weekly update\n"
        "date: 2026-07-05\n"
        "---\n"
        "# Heading\n"
        "\n"
        "Some **prose**.\n"
    )
    content = load_content(f, MANIFEST)
    assert content["title"] == "Weekly update"
    assert content["date"] == "2026-07-05"
    assert "<h1>Heading</h1>" in content["body"]
    assert "<strong>prose</strong>" in content["body"]


def test_markdown_table_extension(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\ntitle: T\n---\n"
        "| A | B |\n"
        "|---|---|\n"
        "| 1 | 2 |\n"
    )
    content = load_content(f, MANIFEST)
    assert "<table>" in content["body"]


def test_markdown_fenced_code_extension(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\n---\n```\ncode here\n```\n")
    content = load_content(f, MANIFEST)
    assert "<pre>" in content["body"]
    assert "<code>" in content["body"]


def test_yaml_content_file(tmp_path):
    f = tmp_path / "brief.yaml"
    f.write_text("title: From YAML\nbody: 'plain **text**'\n")
    content = load_content(f, MANIFEST)
    assert content["title"] == "From YAML"
    assert "<strong>text</strong>" in content["body"]


def test_json_content_file(tmp_path):
    f = tmp_path / "brief.json"
    f.write_text(json.dumps({"title": "From JSON", "body": "hello"}))
    content = load_content(f, MANIFEST)
    assert content["title"] == "From JSON"
    assert "hello" in content["body"]


def test_missing_required_slot_names_it(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ndate: 2026-07-05\n---\n")
    with pytest.raises(ContentError, match="title"):
        load_content(f, MANIFEST)


def test_unknown_key_named(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\nbogus: 1\n---\nbody text\n")
    with pytest.raises(ContentError, match="bogus"):
        load_content(f, MANIFEST)


def test_unknown_key_in_json(tmp_path):
    f = tmp_path / "brief.json"
    f.write_text(json.dumps({"title": "T", "body": "x", "nope": 1}))
    with pytest.raises(ContentError, match="nope"):
        load_content(f, MANIFEST)


def test_body_reserved_in_frontmatter(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\nbody: not allowed here\n---\nreal body\n")
    with pytest.raises(ContentError, match="reserved"):
        load_content(f, MANIFEST)


def test_unterminated_frontmatter(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\nno closing delimiter\n")
    with pytest.raises(ContentError, match="unterminated"):
        load_content(f, MANIFEST)


def test_malformed_yaml_frontmatter_raises_content_error(tmp_path):
    # yaml.YAMLError must surface as a clean ContentError naming the
    # file, not escape as a raw pyyaml traceback.
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: [unclosed\n---\nbody text\n")
    with pytest.raises(ContentError, match=r"brief\.md.*YAML"):
        load_content(f, MANIFEST)


def test_malformed_yaml_file_raises_content_error(tmp_path):
    f = tmp_path / "brief.yaml"
    f.write_text("title: [unclosed\n")
    with pytest.raises(ContentError, match=r"brief\.yaml.*YAML"):
        load_content(f, MANIFEST)


def test_data_slot_item_markdown_rendered(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: T\n"
        "sections:\n"
        "  - heading: First\n"
        "    body: '**bold**'\n"
        "---\n"
        "body text\n"
    )
    content = load_content(f, MANIFEST)
    assert content["sections"][0]["heading"] == "First"
    assert "<strong>bold</strong>" in content["sections"][0]["body"]


def test_data_slot_unknown_item_key_named(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: T\n"
        "sections:\n"
        "  - heading: First\n"
        "    surprise: oops\n"
        "---\n"
        "body text\n"
    )
    with pytest.raises(ContentError, match="surprise"):
        load_content(f, MANIFEST)


def test_data_slot_without_items_schema_passthrough(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\ntags: [alpha, beta]\n---\nbody text\n")
    content = load_content(f, MANIFEST)
    assert content["tags"] == ["alpha", "beta"]


def test_data_slot_must_be_list(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\ntags: not-a-list\n---\nbody text\n")
    with pytest.raises(ContentError, match="tags"):
        load_content(f, MANIFEST)


def test_unsupported_extension(tmp_path):
    f = tmp_path / "brief.txt"
    f.write_text("title: T\n")
    with pytest.raises(ContentError, match="unsupported"):
        load_content(f, MANIFEST)


# --- sanitization of markdown-rendered slots (see sanitize.py) ---

def test_markdown_body_raw_html_payloads_sanitized(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\ntitle: T\n---\n"
        "before\n\n"
        '<img src=x onerror=alert(1)>\n\n'
        "<svg onload=alert(1)><circle/></svg>\n\n"
        '<a href="javascript:alert(1)">click</a>\n\n'
        "after\n"
    )
    content = load_content(f, MANIFEST)
    body = str(content["body"])
    assert "onerror" not in body
    assert "onload" not in body
    assert "<svg" not in body
    assert "javascript:" not in body
    assert "<a>click</a>" in body  # tag survives, href dropped
    assert "before" in body and "after" in body


def test_markdown_body_script_content_removed(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text("---\ntitle: T\n---\n<script>document.write('pwn')</script>\n\nok\n")
    content = load_content(f, MANIFEST)
    body = str(content["body"])
    assert "script" not in body
    assert "document.write" not in body
    assert "ok" in body


def test_data_slot_markdown_item_sanitized(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: T\n"
        "sections:\n"
        "  - heading: First\n"
        "    body: '<img src=x onerror=alert(1)> fine **bold**'\n"
        "---\n"
        "body text\n"
    )
    content = load_content(f, MANIFEST)
    section_body = str(content["sections"][0]["body"])
    assert "onerror" not in section_body
    assert '<img src="x" />' in section_body
    assert "<strong>bold</strong>" in section_body


def test_markdown_rendering_never_consults_entry_points(monkeypatch):
    # Regression: python-markdown resolves STRING extension names by
    # scanning installed entry points first (vendor/markdown/core.py),
    # and on Python < 3.10 that scan imports the importlib_metadata
    # backport -- which jimemo does not vendor. Stock macOS Python
    # 3.9.6 (the documented floor) crashed on first render exactly
    # there, with jimemo doctor all-green (reported by a first-time
    # tester). Extensions are passed as OBJECTS precisely so the
    # entry-point path is never taken; this pins that on every
    # interpreter version by making the scan explode if consulted.
    import markdown.util

    def boom():
        raise ModuleNotFoundError("No module named 'importlib_metadata'")

    monkeypatch.setattr(markdown.util, "get_installed_extensions", boom)

    from jimemo.content import _render_markdown

    html = str(_render_markdown("| a |\n| --- |\n| b |\n\n```\ncode\n```\n"))
    assert "<table>" in html
    assert "<code>" in html


# --- soft line breaks between Japanese characters are dropped (jimemo#saa4) ---

def test_soft_break_between_japanese_characters_is_dropped():
    # A hard-wrapped Japanese paragraph must not render the newline as
    # a space: Japanese has no inter-word spaces.
    assert str(_render_markdown("外部の\n承認待ち")) == "<p>外部の承認待ち</p>"


def test_soft_break_between_kana_joins():
    assert "カタカナひらがな" in str(_render_markdown("カタカナ\nひらがな"))


def test_soft_break_next_to_fullwidth_punctuation_joins():
    assert "括弧（例）続き" in str(_render_markdown("括弧（例）\n続き"))
    assert "一つ、二つ" in str(_render_markdown("一つ、\n二つ"))


def test_soft_break_next_to_latin_keeps_newline():
    # Only the two characters adjacent to the newline decide; a Latin
    # letter, digit, ASCII space or ASCII punctuation on either side
    # keeps it ("the end\nof line" still renders as two words).
    assert "text\n承認待ち" in str(_render_markdown("text\n承認待ち"))
    assert "承認待ち\ntext" in str(_render_markdown("承認待ち\ntext"))
    assert "数字 1,234\n続き" in str(_render_markdown("数字 1,234\n続き"))
    assert "the end\nof line" in str(_render_markdown("the end\nof line"))


def test_soft_break_next_to_hangul_keeps_newline():
    # Korean is written with spaces, so the break stays.
    assert "한국어\n문장" in str(_render_markdown("한국어\n문장"))


def test_soft_break_inside_code_is_kept():
    fenced = str(_render_markdown("```\n外部の\n承認待ち\n```\n"))
    assert "<code>外部の\n承認待ち" in fenced
    inline = str(_render_markdown("`外部の\n承認`"))
    assert "<code>外部の\n承認</code>" in inline


def test_hard_break_survives_with_following_text_intact():
    html = str(_render_markdown("外部の  \n承認"))
    assert "<br" in html
    assert "承認" in html


def test_load_content_joins_japanese_soft_breaks_in_body_and_data_slot(tmp_path):
    f = tmp_path / "brief.md"
    f.write_text(
        "---\n"
        "title: T\n"
        "sections:\n"
        "  - heading: First\n"
        '    body: "外部の\\n承認待ち"\n'
        "---\n"
        "遅れの原因は外部の\n"
        "承認待ちで、来週の月曜日に解消する見込みである。\n"
    )
    content = load_content(f, MANIFEST)
    assert "外部の承認待ち" in content["body"]
    assert "外部の\n承認待ち" not in content["body"]
    assert "外部の承認待ち" in content["sections"][0]["body"]
    assert "外部の\n承認待ち" not in content["sections"][0]["body"]


# --- the join also holds across an inline element's boundary (review of jimemo#saa4) ---

def test_soft_break_after_inline_element_joins():
    # 必要 sits in a <strong>; the "\n" is the first character of its tail.
    assert str(_render_markdown("承認は**必要**\nです")) == "<p>承認は<strong>必要</strong>です</p>"
    assert str(_render_markdown("[承認](https://e.example)\nです")) == (
        '<p><a href="https://e.example">承認</a>です</p>'
    )


def test_soft_break_before_inline_element_joins():
    assert str(_render_markdown("承認は\n**必要**です")) == "<p>承認は<strong>必要</strong>です</p>"
    # The code span's own content is untouched; only the prose newline goes.
    assert str(_render_markdown("外部の\n`コード`です")) == "<p>外部の<code>コード</code>です</p>"


def test_soft_break_between_two_inline_elements_joins():
    assert str(_render_markdown("**あ**\n**い**")) == "<p><strong>あ</strong><strong>い</strong></p>"


def test_soft_break_next_to_inline_element_with_latin_keeps_newline():
    assert str(_render_markdown("**word**\nです")) == "<p><strong>word</strong>\nです</p>"
    assert str(_render_markdown("承認は\n**word**")) == "<p>承認は\n<strong>word</strong></p>"


def test_block_formatting_newlines_are_not_soft_breaks():
    # prettify's "\n" between two <li> is formatting, not prose, even with
    # Japanese on both sides of it.
    assert str(_render_markdown("- 日本\n- 語")) == "<ul>\n<li>日本</li>\n<li>語</li>\n</ul>"


def test_hard_break_newline_before_japanese_is_kept():
    assert str(_render_markdown("外部の  \n承認")) == "<p>外部の<br />\n承認</p>"


def test_halfwidth_hangul_keeps_newline_but_halfwidth_katakana_joins():
    assert "ﾡ\nﾢ" in str(_render_markdown("ﾡ\nﾢ"))          # U+FFA1, U+FFA2: halfwidth Hangul
    assert "ｶﾅです" in str(_render_markdown("ｶﾅ\nです"))      # U+FF76, U+FF85: halfwidth katakana
    assert "２日" in str(_render_markdown("２\n日"))           # fullwidth digit next to Han joins


# --- an image nested in an inline element is a join barrier (jimemo#vpq3) ---

def test_soft_break_before_inline_image_keeps_newline():
    # _first_char must not look past the <img> to the 本 in its tail:
    # an image is rendered content that renders no character, so no
    # character adjoins the break and the newline stays.
    assert "日\n<strong><img" in str(_render_markdown("日\n**![図](plot.png)本**"))


def test_soft_break_after_inline_image_keeps_newline():
    # _last_char must not look past the <img> back to the 日 either.
    assert "</strong>\n本" in str(_render_markdown("**日![図](plot.png)**\n本"))


def test_soft_break_away_from_inline_image_still_joins():
    # The image is not adjacent to the break on either side, so the
    # join fires exactly as before.
    assert "日<strong>本<img" in str(_render_markdown("日\n**本![図](plot.png)**"))
    assert "日</strong>本" in str(_render_markdown("**![図](plot.png)日**\n本"))


def test_first_char_passes_empty_span_but_stops_at_image():
    # Pinned directly on the helper: an element that renders nothing at
    # all (an empty span) is "nothing" and the scan continues to its
    # tail; an <img> is a barrier, exactly as a <br> is.
    strong = ET.Element("strong")
    span = ET.SubElement(strong, "span")
    span.tail = "本"
    assert _first_char(strong) == "本"

    strong = ET.Element("strong")
    img = ET.SubElement(strong, "img")
    img.tail = "本"
    assert _first_char(strong) is None
