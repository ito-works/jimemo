"""The chart init script's theme runtime (jimemo#7n1f), executed.

chart_init_js wraps each chart's construction in a small fixed runtime
that maps the baked DEFAULT_PALETTE onto the page's --jm-chart-N tokens
and repaints when the theme changes. These tests run the REAL init body
under node with stub browser globals (a fake Chart that records what it
was built with, a switchable token map for getComputedStyle, and
captured matchMedia / MutationObserver / print listeners). The live
browser check is done at review time; this pins the logic in the suite.
Skipped when node is not installed.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.charts import (
    DEFAULT_PALETTE,
    build_chart_config,
    chart_init_js,
    serialize_chart_config,
)

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

DARK = ["#3987e5", "#199e70", "#c98500", "#008300",
        "#9085e9", "#e66767", "#d55181", "#d95926"]
DARK_TOKENS = {f"--jm-chart-{i + 1}": c for i, c in enumerate(DARK)}
LIGHT_TOKENS = {f"--jm-chart-{i + 1}": c for i, c in enumerate(DEFAULT_PALETTE)}
# Chart chrome (jimemo#e2sb): text from --jm-muted, lines from --jm-border,
# tick label backdrops (radar) from --jm-surface.
DARK_CHROME = {"--jm-muted": "#a29b8d", "--jm-border": "#34383f", "--jm-surface": "#1e2126"}
LIGHT_CHROME = {"--jm-muted": "#6b6459", "--jm-border": "#e4e0d8", "--jm-surface": "#ffffff"}

# Reads {body, tokens, steps} on stdin. Steps: ["tokens", {...}],
# ["mutate"] (data-theme changed), ["media"] (prefers-color-scheme
# changed), ["beforeprint"], ["afterprint"], ["snap"]. Prints
# {first: <datasets at construction>, options: <options at
# construction>, snaps: [{colors, updates, mode}]}.
DRIVER = r"""
let input = "";
process.stdin.on("data", d => { input += d; });
process.stdin.on("end", () => {
  const {body, tokens, steps} = JSON.parse(input);
  let current = tokens;
  const listeners = {};
  let observer = null;
  global.document = {
    documentElement: {},
    getElementById: id => ({id}),
  };
  global.getComputedStyle = el => {
    if (el !== global.document.documentElement) throw new Error("wrong element");
    return {getPropertyValue: name => current[name] || ""};
  };
  const on = (key, fn) => { (listeners[key] = listeners[key] || []).push(fn); };
  const fire = key => listeners[key].forEach(fn => fn());
  const observers = [];
  global.matchMedia = query => ({
    addEventListener: (type, fn) => { on("media:" + query + ":" + type, fn); },
  });
  global.MutationObserver = function (fn) {
    this.observe = (target, opts) => { observer = {fn, target, opts}; observers.push(observer); };
  };
  global.addEventListener = (type, fn) => { on(type, fn); };
  const charts = [];
  const LIB = {color: "#666", borderColor: "rgba(0,0,0,0.1)"};
  const LIB_BACKDROP = "rgba(255, 255, 255, 0.75)";
  const chrome = () => ({color: Chart.defaults.color, borderColor: Chart.defaults.borderColor,
                         backdrop: Chart.defaults.scale.ticks.backdropColor});
  const scales = o => o.scales === undefined ? null : JSON.parse(JSON.stringify(o.scales));
  global.Chart = function (el, cfg) {
    this.el = el;
    this.data = cfg.data;
    this.options = JSON.parse(JSON.stringify(cfg.options));
    this.updates = 0;
    this.mode = null;
    this.first = JSON.parse(JSON.stringify(cfg.data.datasets));
    this.firstChrome = chrome();
    this.live = cfg.options;
    this.atUpdate = [];
    // Like Chart.js, bake the defaults' colors into cfg.options.scales.
    const merge = () => {
      if (cfg.type === "doughnut" || cfg.type === "pie") { cfg.options.scales = cfg.options.scales || {}; return; }
      const sc = cfg.options.scales = cfg.options.scales || {};
      for (const id of ["x", "y"]) {
        const s = sc[id] = sc[id] || {};
        s.ticks = Object.assign({color: Chart.defaults.color}, s.ticks);
        s.grid = Object.assign({color: Chart.defaults.borderColor}, s.grid);
      }
    };
    merge();
    this.update = mode => {
      this.atUpdate.push({chrome: chrome(), scales: scales(cfg.options)});
      merge();
      this.updates += 1;
      this.mode = mode === undefined ? "default" : mode;
    };
    charts.push(this);
  };
  global.Chart.defaults = Object.assign({scale: {ticks: {backdropColor: LIB_BACKDROP}}}, LIB);
  eval(body);
  const chart = charts[0];
  const colors = ds => ds.map(d => ({bg: d.backgroundColor, border: d.borderColor}));
  const snaps = [];
  for (const [op, arg] of steps) {
    if (op === "tokens") current = arg;
    else if (op === "mutate") observers.forEach(o => o.fn([]));
    else if (op === "media") fire("media:(prefers-color-scheme: dark):change");
    else if (op === "beforeprint" || op === "afterprint") fire(op);
    else if (op === "snap") snaps.push({
      colors: colors(chart.data.datasets), updates: chart.updates, mode: chart.mode,
      chrome: chrome(),
      atUpdate: charts.map(c => c.atUpdate[c.atUpdate.length - 1] || null),
    });
  }
  console.log(JSON.stringify({
    charts: charts.length,
    el: chart.el,
    first: colors(chart.first),
    firstChrome: charts.map(c => c.firstChrome),
    jimemo: global.Chart.jimemo,
    options: chart.options,
    observer: {opts: observer.opts, rootIsHtml: observer.target === global.document.documentElement},
    snaps,
  }));
});
"""


def run(config, tokens, steps=(), extra=()):
    body = chart_init_js("c1", serialize_chart_config(config))
    for i, other in enumerate(extra):
        body += chart_init_js(f"c{i + 2}", serialize_chart_config(other))
    proc = subprocess.run(
        [NODE, "-e", DRIVER],
        input=json.dumps({"body": body, "tokens": tokens, "steps": list(steps)}),
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def bar_config(n=2, palette=None):
    decl = {"id": "c1", "type": "bar", "data_slot": "d"}
    data = {"labels": ["a", "b"],
            "series": [{"name": f"s{i}", "values": [1, 2]} for i in range(n)]}
    return build_chart_config(decl, data, palette=palette)


def pie_config():
    decl = {"id": "c1", "type": "doughnut", "data_slot": "d"}
    data = {"labels": ["a", "b", "c"], "series": [{"name": "s", "values": [1, 2, 3]}]}
    return build_chart_config(decl, data)


def test_first_draw_uses_the_dark_tokens():
    out = run(bar_config(), DARK_TOKENS)
    assert out["charts"] == 1
    # The runtime's first statement unhides the canvas (jimemo#s3e6).
    assert out["el"] == {"id": "c1", "hidden": False}
    assert out["first"] == [
        {"bg": DARK[0], "border": DARK[0]},
        {"bg": DARK[1], "border": DARK[1]},
    ]


def test_first_draw_uses_the_light_tokens():
    out = run(bar_config(), LIGHT_TOKENS)
    assert out["first"][0] == {"bg": DEFAULT_PALETTE[0], "border": DEFAULT_PALETTE[0]}


def test_pie_slices_each_mapped_and_no_border_invented():
    out = run(pie_config(), DARK_TOKENS)
    assert out["first"] == [{"bg": DARK[:3]}]


def test_token_values_are_trimmed():
    out = run(bar_config(1), {"--jm-chart-1": "  #3987e5 "})
    assert out["first"][0]["bg"] == "#3987e5"


def test_empty_token_keeps_the_baked_color():
    out = run(bar_config(2), {"--jm-chart-2": "#199e70"})
    assert out["first"] == [
        {"bg": DEFAULT_PALETTE[0], "border": DEFAULT_PALETTE[0]},
        {"bg": "#199e70", "border": "#199e70"},
    ]


def test_custom_palette_left_alone_but_default_literals_adapt():
    out = run(bar_config(2, palette=["#123456", DEFAULT_PALETTE[0]]), DARK_TOKENS)
    assert out["first"] == [
        {"bg": "#123456", "border": "#123456"},
        # A custom entry equal to a baked value names token slot 1.
        {"bg": DARK[0], "border": DARK[0]},
    ]


def test_data_theme_change_repaints():
    out = run(bar_config(1), DARK_TOKENS,
              [["tokens", LIGHT_TOKENS], ["mutate"], ["snap"]])
    assert out["observer"] == {
        "opts": {"attributes": True, "attributeFilter": ["data-theme"]},
        "rootIsHtml": True,
    }
    # The runtime's own update mode, whose transition is set to duration
    # 0: a synchronous redraw that also refreshes bars' shared options
    # (update("none") does not) and leaves other animations alone.
    assert out["snaps"] == [{
        "colors": [{"bg": DEFAULT_PALETTE[0], "border": DEFAULT_PALETTE[0]}],
        "updates": 1, "mode": "jimemo",
        "chrome": LIB,
        "atUpdate": [{"chrome": LIB, "scales": None}],
    }]


def test_repaint_mode_has_a_zero_duration_transition():
    out = run(bar_config(1), DARK_TOKENS)
    assert out["options"] == {
        "transitions": {"jimemo": {"animation": {"duration": 0}}},
    }


def test_existing_transitions_and_animation_options_are_kept():
    config = bar_config(1)
    config["options"] = {
        "animation": {"duration": 5},
        "transitions": {"active": {"animation": {"duration": 7}}},
    }
    out = run(config, DARK_TOKENS)
    assert out["options"] == {
        "animation": {"duration": 5},
        "transitions": {
            "active": {"animation": {"duration": 7}},
            "jimemo": {"animation": {"duration": 0}},
        },
    }


def test_color_scheme_change_repaints():
    out = run(bar_config(1), LIGHT_TOKENS,
              [["tokens", DARK_TOKENS], ["media"], ["snap"]])
    assert out["snaps"][0]["colors"] == [{"bg": DARK[0], "border": DARK[0]}]
    assert out["snaps"][0]["updates"] == 1


def test_print_uses_the_baked_light_palette_then_restores():
    out = run(pie_config(), DARK_TOKENS,
              [["beforeprint"], ["snap"], ["afterprint"], ["snap"]])
    printed, restored = out["snaps"]
    assert printed["colors"] == [{"bg": list(DEFAULT_PALETTE[:3])}]
    assert restored["colors"] == [{"bg": DARK[:3]}]
    assert restored["updates"] == 2


# --- chart chrome: tick/legend text and grid lines (jimemo#e2sb) ---
# Chart.js 4.5.1 reads tick, legend and scale-title text from
# Chart.defaults.color and grid/axis lines from Chart.defaults.borderColor,
# so the runtime sets those page-wide defaults; the driver's fake Chart
# bakes them into cfg.options.scales at construction and at each update,
# as Chart.js's mergeScaleConfig does.

LIB_BACKDROP = "rgba(255, 255, 255, 0.75)"
LIB = {"color": "#666", "borderColor": "rgba(0,0,0,0.1)", "backdrop": LIB_BACKDROP}
DARK_LIB = {"color": "#a29b8d", "borderColor": "#34383f", "backdrop": "#1e2126"}
LIGHT_LIB = {"color": "#6b6459", "borderColor": "#e4e0d8", "backdrop": "#ffffff"}


def test_first_draw_takes_chrome_colors_from_the_tokens():
    out = run(bar_config(1), {**DARK_TOKENS, **DARK_CHROME})
    assert out["firstChrome"] == [DARK_LIB]
    # The library's own values are kept for print and empty tokens.
    assert out["jimemo"] == ["#666", "rgba(0,0,0,0.1)", LIB_BACKDROP]
    # Nothing is added to the chart's own options.
    assert out["options"] == {"transitions": {"jimemo": {"animation": {"duration": 0}}}}


def test_chrome_tokens_are_trimmed():
    out = run(bar_config(1), {"--jm-muted": " #a29b8d ", "--jm-border": "\t#34383f",
                              "--jm-surface": "#1e2126\n"})
    assert out["firstChrome"] == [DARK_LIB]


def test_empty_chrome_tokens_leave_the_library_defaults():
    out = run(bar_config(1), {**DARK_TOKENS, "--jm-muted": "#a29b8d"})
    assert out["firstChrome"] == [{"color": "#a29b8d", "borderColor": "rgba(0,0,0,0.1)",
                                   "backdrop": LIB_BACKDROP}]


def test_explicit_caller_chrome_options_are_untouched():
    config = bar_config(1)
    config["options"] = {"color": "#111111", "borderColor": "#222222",
                         "scales": {"x": {"ticks": {"color": "#333333"}}}}
    out = run(config, {**DARK_TOKENS, **LIGHT_CHROME},
              [["tokens", {**DARK_TOKENS, **DARK_CHROME}], ["mutate"], ["snap"]])
    assert out["options"]["color"] == "#111111"
    assert out["options"]["borderColor"] == "#222222"
    assert out["options"]["scales"] == {"x": {"ticks": {"color": "#333333"}}}
    # Before the repaint's update, the caller's own scales come back
    # (the baked colors are dropped, the caller's one is kept).
    assert out["snaps"][0]["atUpdate"] == [{
        "chrome": DARK_LIB,
        "scales": {"x": {"ticks": {"color": "#333333"}}},
    }]


def test_doughnut_gets_no_options_and_no_cartesian_scales():
    out = run(pie_config(), {**DARK_TOKENS, **DARK_CHROME},
              [["mutate"], ["snap"]])
    assert out["options"] == {"transitions": {"jimemo": {"animation": {"duration": 0}}}}
    assert out["firstChrome"] == [DARK_LIB]
    assert out["snaps"][0]["atUpdate"] == [{"chrome": DARK_LIB, "scales": None}]


def test_theme_change_repaints_the_chrome_before_the_update():
    out = run(bar_config(1), {**LIGHT_TOKENS, **LIGHT_CHROME},
              [["tokens", {**DARK_TOKENS, **DARK_CHROME}], ["mutate"], ["snap"],
               ["tokens", {**LIGHT_TOKENS, **LIGHT_CHROME}], ["media"], ["snap"]])
    dark, light = out["snaps"]
    # The defaults are set and the baked scales dropped before update().
    assert dark["atUpdate"] == [{"chrome": DARK_LIB, "scales": None}]
    assert dark["mode"] == "jimemo" and dark["updates"] == 1
    assert light["atUpdate"] == [{"chrome": LIGHT_LIB, "scales": None}]
    assert light["updates"] == 2


def test_print_uses_the_library_chrome_then_restores():
    out = run(bar_config(1), {**DARK_TOKENS, **DARK_CHROME},
              [["beforeprint"], ["snap"], ["afterprint"], ["snap"]])
    printed, restored = out["snaps"]
    # Print is always light: the library's own dark-on-white defaults.
    assert printed["atUpdate"] == [{"chrome": LIB, "scales": None}]
    assert restored["atUpdate"] == [{"chrome": DARK_LIB, "scales": None}]


def test_a_second_chart_does_not_take_the_tokens_for_the_library_values():
    out = run(bar_config(1), {**DARK_TOKENS, **DARK_CHROME},
              [["beforeprint"], ["snap"]], extra=[pie_config()])
    assert out["charts"] == 2
    assert out["firstChrome"] == [DARK_LIB, DARK_LIB]
    assert out["jimemo"] == ["#666", "rgba(0,0,0,0.1)", LIB_BACKDROP]
    # Both charts print with the library's values.
    assert out["snaps"][0]["atUpdate"] == [
        {"chrome": LIB, "scales": None},
        {"chrome": LIB, "scales": None},
    ]


# A radar's radialLinear scale draws its tick labels over a backdrop
# (ticks.showLabelBackdrop), whose color Chart.js 4.5.1 takes from
# Chart.defaults.scale.ticks.backdropColor, rgba(255, 255, 255, 0.75).
# Light --jm-muted text on that white box is lower contrast than the
# library's #666 was, so the runtime themes the backdrop from --jm-surface.

def radar_config():
    decl = {"id": "c1", "type": "radar", "data_slot": "d"}
    data = {"labels": ["a", "b", "c"], "series": [{"name": "s", "values": [1, 2, 3]}]}
    return build_chart_config(decl, data)


def test_radar_tick_backdrop_follows_the_surface_and_repaints():
    out = run(radar_config(), {**DARK_TOKENS, **DARK_CHROME},
              [["tokens", {**LIGHT_TOKENS, **LIGHT_CHROME}], ["mutate"], ["snap"],
               ["beforeprint"], ["snap"]])
    assert out["firstChrome"] == [DARK_LIB]
    light, printed = out["snaps"]
    assert light["atUpdate"][0]["chrome"] == LIGHT_LIB
    assert printed["atUpdate"][0]["chrome"] == LIB
    # No scale is added for it: the caller's config stays as built.
    assert "scales" not in out["options"]


def test_explicit_caller_backdrop_is_kept_through_a_repaint():
    config = radar_config()
    config["options"] = {"scales": {"r": {"ticks": {"backdropColor": "#123456"}}}}
    out = run(config, {**DARK_TOKENS, **DARK_CHROME}, [["mutate"], ["snap"]])
    assert out["options"]["scales"] == {"r": {"ticks": {"backdropColor": "#123456"}}}
    assert out["snaps"][0]["atUpdate"][0]["scales"]["r"] == {
        "ticks": {"backdropColor": "#123456"}}
