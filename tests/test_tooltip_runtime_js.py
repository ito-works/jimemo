"""The tooltip script (toolkit/tooltip.js, jimemo#7h7c), executed.

Runs the REAL script body under node against a small stub DOM (elements
with attributes, closest/contains, captured document and window
listeners) and drives it with mouse, touch, keyboard, Escape and scroll
events. The live-browser check is done at review time; this pins the
logic in the suite. Skipped when node is not installed.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jimemo.tooltip import tooltip_js_text

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# Reads {body, steps} on stdin; prints one snapshot per ["snap"] step and
# the preventDefault flag of every ["click", ...] step.
DRIVER = r"""
let input = "";
process.stdin.on("data", d => { input += d; });
process.stdin.on("end", () => {
  const {body, steps} = JSON.parse(input);
  class El {
    constructor(tag, attrs, parent) {
      this.tag = tag; this.attrs = Object.assign({}, attrs || {});
      this.parent = null; this.children = []; this.style = {};
      this.hidden = false; this.textContent = "";
      this.offsetWidth = 100; this.offsetHeight = 40;
      this.listeners = {};
      if (parent) parent.appendChild(this);
    }
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
    get id() { return this.attrs.id; }
    set id(v) { this.attrs.id = String(v); }
    set className(v) { this.attrs["class"] = v; }
    getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }
    setAttribute(n, v) { this.attrs[n] = String(v); }
    removeAttribute(n) { delete this.attrs[n]; }
    hasAttribute(n) { return n in this.attrs; }
    appendChild(c) { c.parent = this; this.children.push(c); return c; }
    contains(o) { for (let e = o; e; e = e.parent) if (e === this) return true; return false; }
    matches(sel) {
      if (sel === "[data-tip]") return this.hasAttribute("data-tip");
      if (sel === "a[href]") return this.tag === "a" && this.hasAttribute("href");
      if (sel === "[title]") return this.hasAttribute("title");
      throw new Error("unsupported selector " + sel);
    }
    closest(sel) { for (let e = this; e; e = e.parent) if (e.matches(sel)) return e; return null; }
    all() { return this.children.flatMap(c => [c, ...c.all()]); }
    querySelectorAll(sel) { return this.all().filter(e => e.matches(sel)); }
  }
  const root = new El("html");
  root.clientWidth = 300; root.clientHeight = 200;
  const bodyEl = new El("body", {}, root);
  const named = {
    span: new El("span", {title: "one\ntwo"}, bodyEl),
    link: new El("a", {href: "#x", title: "link tip"}, bodyEl),
    taken: new El("div", {id: "jm-tip"}, bodyEl),
    plain: new El("p", {}, bodyEl),
    abbr: new El("abbr", {title: "Reflexes", "aria-describedby": "note"}, bodyEl),
    empty: new El("img", {title: ""}, bodyEl),
  };
  named.outer = new El("span", {title: "outer"}, bodyEl);
  named.inner = new El("b", {title: "inner"}, named.outer);
  const docListeners = {}, winListeners = {};
  global.document = {
    documentElement: root, body: bodyEl,
    createElement: tag => new El(tag),
    getElementById: id => root.all().find(e => e.attrs.id === id) || null,
    addEventListener: (t, fn) => { docListeners[t] = fn; },
  };
  global.window = {addEventListener: (t, fn) => { winListeners[t] = fn; }};
  eval(body);
  const tip = bodyEl.children.find(e => e.attrs["class"] === "jm-tip");
  const out = {snaps: [], prevented: []};
  const ev = (o) => Object.assign({preventDefault() { this.defaultPrevented = true; }}, o);
  for (const step of steps) {
    const [kind, ...a] = step;
    if (kind === "pointer") docListeners.pointerdown(ev({pointerType: a[0]}));
    else if (kind === "pointerover") docListeners.pointerover(ev({pointerType: a[0]}));
    else if (kind === "pointermove") docListeners.pointermove(ev({pointerType: a[0]}));
    else if (kind === "over") docListeners.mouseover(ev({target: named[a[0]], clientX: a[1], clientY: a[2]}));
    else if (kind === "move") docListeners.mousemove(ev({clientX: a[0], clientY: a[1]}));
    else if (kind === "out") docListeners.mouseout(ev({target: named[a[0]], relatedTarget: a[1] ? named[a[1]] : null}));
    else if (kind === "click") {
      const e = ev({target: named[a[0]], clientX: a[1], clientY: a[2], detail: a[3]});
      // Bubble: element listeners from the target up, then the document.
      for (let el = e.target; el; el = el.parent) {
        for (const fn of el.listeners.click || []) fn(e);
      }
      docListeners.click(e);
      out.prevented.push(!!e.defaultPrevented);
    }
    else if (kind === "key") docListeners.keydown(ev({key: a[0]}));
    else if (kind === "scroll") winListeners.scroll(ev({}));
    else if (kind === "size") { tip.offsetWidth = a[0]; tip.offsetHeight = a[1]; }
    else if (kind === "snap") {
      const attrs = {};
      for (const [k, e] of Object.entries(named)) attrs[k] = Object.assign({}, e.attrs);
      out.snaps.push({hidden: tip.hidden, text: tip.textContent, id: tip.attrs.id,
        role: tip.attrs.role, left: tip.style.left, top: tip.style.top, attrs});
    }
  }
  process.stdout.write(JSON.stringify(out));
});
"""


def run(steps):
    proc = subprocess.run(
        [NODE, "-e", DRIVER],
        input=json.dumps({"body": tooltip_js_text(), "steps": steps}),
        capture_output=True, text=True, timeout=30, check=True,
    )
    return json.loads(proc.stdout)


def test_load_moves_titles_and_picks_an_unused_id():
    snap = run([["snap"]])["snaps"][0]
    assert snap["hidden"] is True and snap["role"] == "tooltip"
    assert snap["id"] == "jm-tip-2"  # "jm-tip" already belongs to the page
    for name in ("span", "link", "abbr"):
        assert "title" not in snap["attrs"][name]
    assert snap["attrs"]["span"]["data-tip"] == "one\ntwo"
    # An empty title shows nothing: left alone, never a data-tip.
    assert "data-tip" not in snap["attrs"]["empty"]


def test_mouse_hover_shows_places_and_hides():
    out = run([
        ["pointer", "mouse"], ["over", "span", 10, 10], ["snap"],
        ["move", 290, 190], ["snap"],
        ["out", "span", "plain"], ["snap"],
    ])
    shown, flipped, gone = out["snaps"]
    assert shown["hidden"] is False and shown["text"] == "one\ntwo"
    assert (shown["left"], shown["top"]) == ("22px", "22px")
    assert shown["attrs"]["span"]["aria-describedby"] == "jm-tip-2"
    # Past the right/bottom edge the box flips to the other side.
    assert (flipped["left"], flipped["top"]) == ("178px", "138px")
    assert gone["hidden"] is True
    assert "aria-describedby" not in gone["attrs"]["span"]


def test_box_is_clamped_inside_the_viewport():
    out = run([["size", 280, 190], ["over", "span", 150, 100], ["snap"]])
    snap = out["snaps"][0]
    assert (snap["left"], snap["top"]) == ("4px", "4px")


def test_existing_aria_describedby_is_appended_then_restored():
    out = run([["over", "abbr", 5, 5], ["snap"], ["out", "abbr", None], ["snap"]])
    shown, gone = out["snaps"]
    assert shown["attrs"]["abbr"]["aria-describedby"] == "note jm-tip-2"
    assert gone["attrs"]["abbr"]["aria-describedby"] == "note"


def test_touch_tap_toggles_and_ignores_compat_mouse_events():
    out = run([
        ["pointer", "touch"], ["over", "span", 10, 10], ["snap"],
        ["click", "span", 10, 10, 1], ["snap"],
        ["click", "span", 10, 10, 1], ["snap"],
        ["click", "span", 10, 10, 1], ["click", "plain", 50, 50, 1], ["snap"],
    ])
    compat, tapped, toggled, outside = out["snaps"]
    assert compat["hidden"] is True
    assert tapped["hidden"] is False and tapped["text"] == "one\ntwo"
    assert toggled["hidden"] is True
    assert outside["hidden"] is True
    assert out["prevented"] == [False, False, False, False]


def test_pen_counts_as_touch():
    out = run([["pointer", "pen"], ["over", "span", 1, 1], ["snap"],
               ["click", "span", 1, 1, 1], ["snap"]])
    assert [s["hidden"] for s in out["snaps"]] == [True, False]


def test_touch_on_a_link_shows_first_then_follows():
    out = run([
        ["pointer", "touch"],
        ["click", "link", 10, 10, 1], ["snap"],
        ["click", "link", 10, 10, 1], ["snap"],
    ])
    first, second = out["snaps"]
    assert out["prevented"] == [True, False]
    assert first["hidden"] is False and first["text"] == "link tip"
    assert second["hidden"] is False


def test_mouse_and_keyboard_clicks_are_never_intercepted():
    out = run([
        ["pointer", "mouse"], ["click", "link", 10, 10, 1],
        ["pointer", "touch"], ["click", "link", 0, 0, 0], ["snap"],
    ])
    assert out["prevented"] == [False, False]
    assert out["snaps"][0]["hidden"] is True


@pytest.mark.parametrize("dismiss", (["key", "Escape"], ["scroll"]))
def test_escape_and_scroll_hide(dismiss):
    out = run([["over", "span", 10, 10], dismiss, ["snap"]])
    snap = out["snaps"][0]
    assert snap["hidden"] is True
    assert "aria-describedby" not in snap["attrs"]["span"]


def test_hover_resumes_after_a_tap_on_a_hybrid_device():
    out = run([
        ["pointer", "touch"], ["click", "span", 10, 10, 1], ["click", "plain", 50, 50, 1],
        # The mouse moves next: pointerover/pointermove precede mouseover.
        ["pointerover", "mouse"], ["over", "abbr", 20, 20], ["snap"],
        ["pointermove", "mouse"], ["move", 30, 30], ["snap"],
    ])
    hovered, moved = out["snaps"]
    assert hovered["hidden"] is False and hovered["text"] == "Reflexes"
    assert (moved["left"], moved["top"]) == ("42px", "42px")


def test_tap_listener_is_on_each_tipped_element_and_acts_once():
    # A nested tipped element: inner, outer and document listeners all see
    # the tap; only the first acts, so the tip shows rather than toggling
    # off again.
    out = run([["pointer", "touch"], ["click", "inner", 5, 5, 1], ["snap"]])
    snap = out["snaps"][0]
    assert snap["hidden"] is False and snap["text"] == "inner"
