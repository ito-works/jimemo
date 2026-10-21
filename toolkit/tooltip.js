/* jimemo tooltip (jimemo#7h7c). Fixed text shipped by jimemo, emitted by
   the renderer only when content carries a title; `jimemo check` accepts
   this body byte-for-byte and no other. It moves each title to data-tip
   (so the browser's own tooltip does not also appear) and shows one
   shared box: on hover for a mouse, on tap for touch or pen. Text is set
   with textContent only. */
(function () {
  "use strict";
  var doc = document;
  var root = doc.documentElement;
  var els = doc.body.querySelectorAll("[title]");
  var tipped = [];
  for (var i = 0; i < els.length; i++) {
    var t = els[i].getAttribute("title");
    if (!t) continue;
    els[i].setAttribute("data-tip", t);
    els[i].removeAttribute("title");
    tipped.push(els[i]);
  }
  if (!tipped.length) return;

  var id = "jm-tip";
  for (var n = 2; doc.getElementById(id); n++) id = "jm-tip-" + n;
  var tip = doc.createElement("div");
  tip.className = "jm-tip";
  tip.id = id;
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  doc.body.appendChild(tip);

  var current = null;
  var priorDescribedBy = null;
  var touch = false;

  function place(x, y) {
    var gap = 12, edge = 4;
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var vw = root.clientWidth, vh = root.clientHeight;
    var left = x + gap, top = y + gap;
    if (left + w > vw - edge) left = x - gap - w;
    if (top + h > vh - edge) top = y - gap - h;
    left = Math.max(edge, Math.min(left, vw - w - edge));
    top = Math.max(edge, Math.min(top, vh - h - edge));
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }

  function show(el, x, y) {
    if (current !== el) {
      hide();
      current = el;
      priorDescribedBy = el.getAttribute("aria-describedby");
      el.setAttribute("aria-describedby",
        priorDescribedBy ? priorDescribedBy + " " + id : id);
      tip.textContent = el.getAttribute("data-tip");
      tip.hidden = false;
    }
    place(x, y);
  }

  function hide() {
    if (!current) return;
    if (priorDescribedBy === null) current.removeAttribute("aria-describedby");
    else current.setAttribute("aria-describedby", priorDescribedBy);
    current = null;
    priorDescribedBy = null;
    tip.hidden = true;
  }

  function tipOf(target) {
    return target && target.closest ? target.closest("[data-tip]") : null;
  }

  // The input in use, from every pointer event (each precedes the mouse
  // event it causes), so a hybrid device switches back to hover the
  // moment the mouse moves after a tap.
  function sense(e) {
    touch = e.pointerType === "touch" || e.pointerType === "pen";
  }
  doc.addEventListener("pointerdown", sense, true);
  // A touch outside every tipped element closes the box. This rides on
  // pointerdown, not click: iOS Safari delivers a tap on plain text to
  // a document pointerdown listener but not to a document click one.
  doc.addEventListener("pointerdown", function (e) {
    if (touch && current && !tipOf(e.target)) hide();
  }, true);
  doc.addEventListener("pointerover", sense, true);
  doc.addEventListener("pointermove", sense, true);

  doc.addEventListener("mouseover", function (e) {
    if (touch) return;
    var el = tipOf(e.target);
    if (el) show(el, e.clientX, e.clientY);
    else hide();
  });
  doc.addEventListener("mousemove", function (e) {
    if (!touch && current) place(e.clientX, e.clientY);
  });
  doc.addEventListener("mouseout", function (e) {
    if (touch || !current) return;
    var to = e.relatedTarget;
    if (!to || !current.contains(to)) hide();
  });

  // Taps. The listener sits on each tipped element as well as on the
  // document: iOS Safari does not deliver a tap on a non-interactive
  // element to a document listener, but does deliver it to one on the
  // element itself. `jmTipHandled` keeps nested tipped elements and the
  // document listener from acting on the same tap twice.
  function tap(e) {
    if (e.jmTipHandled) return;
    e.jmTipHandled = true;
    if (!touch || e.detail === 0) return;
    var el = tipOf(e.target);
    if (!el) { hide(); return; }
    if (el === current) {
      if (!el.closest("a[href]")) hide();
      return;
    }
    if (el.closest("a[href]")) e.preventDefault();
    show(el, e.clientX, e.clientY);
  }
  for (var k = 0; k < tipped.length; k++) tipped[k].addEventListener("click", tap);
  doc.addEventListener("click", tap);

  doc.addEventListener("keydown", function (e) {
    if (e.key === "Escape") hide();
  });
  window.addEventListener("scroll", hide, true);
  window.addEventListener("resize", hide);
})();
