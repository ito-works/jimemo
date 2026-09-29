"""The script-free chart fallback (jimemo#s3e6).

A chart page rendered with its data is static-first: the canvas starts
``hidden`` and an open ``<details class="jm-chart-data">`` table follows
it, so a reader that disables or CSP-blocks scripts sees every label,
series name and value. The init runtime unhides the canvas and collapses
the table once the chart is built. These tests pin the markup, the
runtime shapes lint recognizes (including a FROZEN copy of the 0.0.3
body, not one derived from chart_init_js), and the runtime's behaviour
under node.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo._paths import REPO_ROOT
from jimemo.charts import (
    _INIT_JS_PREFIX,
    build_chart_config,
    chart_init_js,
    parse_chart_init_js,
    serialize_chart_config,
)
from jimemo.cli import main
from jimemo.content import load_content
from jimemo.lint import lint_html
from jimemo.manifest import load_manifest
from jimemo.render import render_page

from jinja2 import Environment, FileSystemLoader  # noqa: E402 (vendored; on path via jimemo.render)

TEMPLATE = REPO_ROOT / "templates" / "chart-dashboard"
SAMPLE = TEMPLATE / "sample" / "content.yaml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
# chart_init_js("sales", '{"type":"bar"}') exactly as jimemo 0.0.3
# (origin/main at 404cf8c, the jimemo#7n1f runtime) emitted it.
FROZEN_003 = (FIXTURES / "chart-init-0.0.3-sales.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _render(content=None):
    manifest = load_manifest(TEMPLATE)
    content = content if content is not None else load_content(SAMPLE, manifest)
    return render_page(TEMPLATE, content)


def _macro(**kwargs):
    env = Environment(loader=FileSystemLoader(str(REPO_ROOT / "toolkit")),
                      autoescape=True)
    ui = env.get_template("macros.html.j2").module
    return str(ui.chart(**kwargs))


# --- markup -----------------------------------------------------------------

def test_sample_chart_blocks_are_static_first():
    html = _render()
    for chart_id in ("signups-trend", "revenue-by-tier"):
        block = re.search(
            rf'<canvas id="{chart_id}"(.*?)</canvas>\s*(<details.*?</details>)\s*<script>',
            html, re.S,
        )
        assert block, chart_id
        assert " hidden" in block.group(1)
        assert 'role="img"' in block.group(1)
        assert block.group(2).startswith('<details class="jm-chart-data" open>')


def test_sample_fallback_carries_every_label_series_and_value():
    html = _render()
    manifest = load_manifest(TEMPLATE)
    content = load_content(SAMPLE, manifest)
    tables = re.findall(r'<details class="jm-chart-data" open>(.*?)</details>', html, re.S)
    assert len(tables) == 2
    for table, slot in zip(tables, ("chart_data_line", "chart_data_bar")):
        data = content[slot][0]
        for label in data["labels"]:
            assert f'<th scope="row">{label}</th>' in table
        for series in data["series"]:
            name = series["name"].replace("&", "&amp;")
            assert f">{name}</th>" in table
            for value in series["values"]:
                assert f">{value}</td>" in table
    # The unit travels in the series name.
    assert "MRR ($k)" in tables[1]


def test_rendered_sample_passes_standalone_check(tmp_path, capsys):
    out = tmp_path / "out.html"
    out.write_text(_render(), encoding="utf-8")
    assert main(["check", str(out)]) == 0


def test_fallback_text_is_escaped_and_page_still_lints():
    manifest = load_manifest(TEMPLATE)
    content = load_content(SAMPLE, manifest)
    content["chart_data_bar"] = [{
        "labels": ["<b>x</b>", "a&b"],
        "series": [{"name": "<i>n</i>", "values": [1, 2.5]}],
    }]
    html = _render(content)
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert "&lt;i&gt;n&lt;/i&gt;" in html
    assert "a&amp;b" in html
    errors, _ = lint_html(html, manifest)
    assert errors == []


def test_macro_without_data_is_byte_identical_to_the_previous_macro():
    env = Environment(autoescape=True)
    old = env.from_string(
        '{% macro chart(chart_id, init_js) -%}\n'
        '<canvas id="{{ chart_id }}"></canvas>\n'
        '<script>{{ init_js }}</script>\n'
        '{%- endmacro %}'
    ).module
    assert _macro(chart_id="c1", init_js="X") == str(old.chart("c1", "X"))


def test_macro_title_defaults_to_the_chart_id():
    out = _macro(chart_id="c1", init_js="X",
                 data={"labels": ["a"], "series": [{"name": "s", "values": [1]}]})
    assert 'aria-label="c1"' in out
    assert '<caption class="jm-data-table__caption">c1</caption>' in out
    assert "None" not in out


# --- init shapes ------------------------------------------------------------

def test_new_body_uses_the_fallback_prefix():
    body = chart_init_js("sales", '{"type":"bar"}')
    assert body.startswith(_INIT_JS_PREFIX)
    assert "el.hidden=!1;" in body
    # The collapse runs right after construction, before any listener.
    assert body.index("ch=new Chart(el,cfg);") < body.index("f.open=!1") < body.index("matchMedia")


def test_frozen_003_body_differs_from_the_new_body():
    assert FROZEN_003 != chart_init_js("sales", '{"type":"bar"}')
    assert "el.hidden" not in FROZEN_003


@pytest.mark.parametrize("body", [
    FROZEN_003,
    chart_init_js("sales", '{"type":"bar"}'),
    'new Chart(document.getElementById("sales"), {"type":"bar"});',
])
def test_every_generation_is_recognized(body):
    assert parse_chart_init_js(body) == ("sales", '{"type":"bar"}')


def test_frozen_003_body_passes_standalone_lint():
    page = f'<html><body><canvas id="sales"></canvas><script>{FROZEN_003}</script></body></html>'
    errors, _ = lint_html(page, {"charts": ["sales"]})
    assert errors == []


def test_frozen_003_body_with_an_edited_runtime_is_rejected():
    edited = FROZEN_003.replace("beforeprint", "load", 1)
    page = f'<html><body><canvas id="sales"></canvas><script>{edited}</script></body></html>'
    errors, _ = lint_html(page, {"charts": ["sales"]})
    assert any("unexpected inline" in e for e in errors)


def test_a_003_rendered_page_still_passes_check(tmp_path):
    # Rebuild what 0.0.3 wrote for the sample: bare canvases and the
    # 7n1f init body (frozen prefix + the same id and config bytes).
    html = _render()
    html = re.sub(r'<canvas id="([^"]+)" hidden[^>]*></canvas>\s*<details.*?</details>',
                  r'<canvas id="\1"></canvas>', html, flags=re.S)
    frozen_prefix = FROZEN_003[: FROZEN_003.index('sales"), ')]
    html = html.replace(_INIT_JS_PREFIX, frozen_prefix)
    assert "el.hidden" not in html and "jm-chart-data\"" not in html
    out = tmp_path / "old.html"
    out.write_text(html, encoding="utf-8")
    assert main(["check", str(out)]) == 0


# --- the runtime, executed --------------------------------------------------

DRIVER = r"""
let input = "";
process.stdin.on("data", d => { input += d; });
process.stdin.on("end", () => {
  const {body, sibling, chartThrows, mediaThrows} = JSON.parse(input);
  const details = {classList: {contains: c => c === "jm-chart-data"}, open: true};
  const other = {classList: {contains: () => false}, open: true};
  const canvas = {hidden: true, nextElementSibling:
    sibling === "details" ? details : sibling === "other" ? other : null};
  global.document = {documentElement: {}, getElementById: () => canvas};
  global.getComputedStyle = () => ({getPropertyValue: () => ""});
  global.matchMedia = () => {
    if (mediaThrows) throw new Error("no matchMedia");
    return {addEventListener: () => {}};
  };
  global.MutationObserver = function () { this.observe = () => {}; };
  global.addEventListener = () => {};
  global.Chart = function () { if (chartThrows) throw new Error("boom"); this.update = () => {}; };
  let threw = false;
  try { eval(body); } catch (e) { threw = true; }
  console.log(JSON.stringify({threw, hidden: canvas.hidden,
    detailsOpen: details.open, otherOpen: other.open}));
});
"""


def _run(**state):
    config = build_chart_config(
        {"id": "c1", "type": "bar", "data_slot": "d"},
        {"labels": ["a"], "series": [{"name": "s", "values": [1]}]},
    )
    body = chart_init_js("c1", serialize_chart_config(config))
    proc = subprocess.run(
        [NODE, "-e", DRIVER], input=json.dumps({"body": body, **state}),
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


node_only = pytest.mark.skipif(NODE is None, reason="node is not installed")


@node_only
def test_drawn_chart_unhides_canvas_and_collapses_table():
    r = _run(sibling="details")
    assert r == {"threw": False, "hidden": False, "detailsOpen": False, "otherOpen": True}


@node_only
def test_failed_construction_leaves_the_table_open():
    r = _run(sibling="details", chartThrows=True)
    assert r["threw"] and r["detailsOpen"] is True


@node_only
def test_later_listener_failure_still_collapses_the_table():
    r = _run(sibling="details", mediaThrows=True)
    assert r["threw"] and r["detailsOpen"] is False


@node_only
def test_other_or_no_sibling_is_left_alone():
    assert _run(sibling="other")["otherOpen"] is True
    assert _run(sibling=None)["threw"] is False
