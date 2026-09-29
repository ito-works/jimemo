"""The phone-reader fixture set (jimemo#s3e6) renders and passes lint.

tests/fixtures/phone-reader/ holds synthetic content (Japanese and
English prose, a table wider than a phone, an inline SVG) that
shots.sh renders and screenshots at a 390px mobile viewport. The
screenshots need a browser; this test only pins that the fixtures stay
renderable and self-contained, so shots.sh never breaks silently.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._paths import REPO_ROOT
from jimemo.content import load_content
from jimemo.lint import lint_html
from jimemo.manifest import load_manifest
from jimemo.render import render_page

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "phone-reader"


def test_briefing_fixture_renders_with_table_svg_and_japanese():
    template = REPO_ROOT / "templates" / "briefing"
    manifest = load_manifest(template)
    content_path = FIXTURES / "briefing-ja.md"
    html = render_page(template, load_content(content_path, manifest),
                       base_dir=FIXTURES)
    assert "<table>" in html
    assert "<svg" in html
    assert "週次ブリーフィング" in html
    errors, _ = lint_html(html, manifest)
    assert errors == []


def test_narrow_screens_scroll_prose_tables_in_their_own_box():
    css = (REPO_ROOT / "toolkit" / "base.css").read_text(encoding="utf-8")
    block = css[css.index("@media (max-width: 40rem)"):]
    block = block[: block.index("}\n}") + 3]
    assert ".jm-prose table" in block
    assert "overflow-x: auto" in block
