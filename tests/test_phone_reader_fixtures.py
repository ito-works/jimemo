"""The phone-reader fixture set (jimemo#s3e6) renders and passes lint.

tests/fixtures/phone-reader/ holds synthetic content (Japanese and
English prose, a table wider than a phone, an inline SVG) that
shots.sh renders and screenshots at a 390px mobile viewport. The
screenshots need a browser; this test only pins that the fixtures stay
renderable and self-contained, so shots.sh never breaks silently.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._paths import REPO_ROOT
from jimemo.content import TABLE_SCROLL_OPEN, _render_markdown, _wrap_tables, load_content
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


def test_markdown_tables_are_wrapped_in_a_scroll_box():
    html = str(_render_markdown("| a | b |\n|---|---|\n| 1 | 2 |"))
    assert html.startswith(TABLE_SCROLL_OPEN + "<table>")
    assert html.endswith("</table></div>")
    css = (REPO_ROOT / "toolkit" / "base.css").read_text(encoding="utf-8")
    assert ".jm-prose__table-scroll {\n  overflow-x: auto;" in css


def test_nested_tables_get_one_wrapper():
    html = _wrap_tables("<table><tr><td><table></table></td></tr></table>")
    assert html.count(TABLE_SCROLL_OPEN) == 1
    assert html.count("</div>") == 1


@pytest.mark.parametrize("fragment", [
    "<table><tr><td>x\n\npara",          # unclosed
    "<p></table> stray</p>",               # stray close
    "<table></table></table><table>",      # goes negative
])
def test_unbalanced_tables_are_left_unwrapped(fragment):
    # A wrapper must never close an element the fragment did not open.
    assert _wrap_tables(fragment) == fragment


def test_shot_scripts_parse():
    assert subprocess.run(["bash", "-n", str(FIXTURES / "shots.sh")]).returncode == 0
    node = shutil.which("node")
    if node:
        proc = subprocess.run([node, "--check", str(FIXTURES / "shot.mjs")],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


def test_shots_refuses_an_output_dir_inside_the_repo(tmp_path):
    # Refused before any browser is looked for, so this needs none. A
    # fresh subdirectory: the refusal must not depend on it existing.
    target = FIXTURES / "never-created-by-this-test"
    proc = subprocess.run(["bash", str(FIXTURES / "shots.sh"), str(target)],
                          capture_output=True, text=True,
                          env={**os.environ, "CHROME": str(tmp_path / "none")})
    assert proc.returncode == 2, proc.stderr
    assert "inside the repo" in proc.stderr
    assert not target.exists()
