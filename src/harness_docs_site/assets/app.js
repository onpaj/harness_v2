/* Architecture Explorer — fully client-side, no network calls. */
(function () {
  "use strict";

  // ---- Theme (every page) ----
  var THEME_KEY = "harness-docs-theme";
  function applyTheme(t) { document.documentElement.dataset.theme = t; }
  (function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) {}
    if (saved) applyTheme(saved);
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      applyTheme(next);
      try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
    });
  })();

  // ---- Explorer (index only) ----
  var svg = document.getElementById("hexmap");
  var modelEl = document.getElementById("model-data");
  if (!svg || !modelEl) return; // doc page: theme only

  var model = JSON.parse(modelEl.textContent);
  var adrHtml = JSON.parse(document.getElementById("adr-html").textContent || "{}");
  var partsById = {};
  model.parts.forEach(function (p) { partsById[p.id] = p; });

  var token = document.getElementById("token");
  var caption = document.getElementById("caption");
  var playBtn = document.getElementById("play");
  var stepBtn = document.getElementById("step");
  var drawer = document.getElementById("drawer");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function partGroup(id) { return svg.querySelector('.part[data-part-id="' + id + '"]'); }
  function edgeLine(a, b) {
    return svg.querySelector('.edge[data-src="' + a + '"][data-dst="' + b + '"]') ||
           svg.querySelector('.edge[data-src="' + b + '"][data-dst="' + a + '"]');
  }
  function clearHighlights() {
    svg.querySelectorAll(".part.active").forEach(function (g) { g.classList.remove("active"); });
    svg.querySelectorAll(".edge.lit").forEach(function (e) { e.classList.remove("lit"); });
  }
  function highlightStage(i) {
    var stage = model.flow[i];
    var p = partsById[stage.part_id];
    var g = partGroup(stage.part_id);
    if (g) g.classList.add("active");
    if (i > 0) {
      var e = edgeLine(model.flow[i - 1].part_id, stage.part_id);
      if (e) e.classList.add("lit");
    }
    if (caption) caption.textContent = stage.caption;
    if (token && p) { token.setAttribute("cx", p.x); token.setAttribute("cy", p.y); }
  }

  var stageIndex = 0;
  var rafId = null;
  var playing = false;

  function stopMotion() { if (rafId) cancelAnimationFrame(rafId); rafId = null; }
  function setPlayLabel() { if (playBtn) playBtn.textContent = playing ? "⏸ Pause" : "▶ Play"; }

  function animateTo(i, done) {
    if (reduce || i === 0) { highlightStage(i); done(); return; }
    var from = partsById[model.flow[i - 1].part_id];
    var to = partsById[model.flow[i].part_id];
    var start = null;
    var dur = 900;
    if (token) token.classList.add("running");
    var e = edgeLine(model.flow[i - 1].part_id, model.flow[i].part_id);
    if (e) e.classList.add("lit");
    function frame(ts) {
      if (start === null) start = ts;
      var t = Math.min(1, (ts - start) / dur);
      if (token) {
        token.setAttribute("cx", from.x + (to.x - from.x) * t);
        token.setAttribute("cy", from.y + (to.y - from.y) * t);
      }
      if (t < 1) { rafId = requestAnimationFrame(frame); }
      else { highlightStage(i); done(); }
    }
    rafId = requestAnimationFrame(frame);
  }

  function play() {
    playing = true; setPlayLabel();
    if (token) token.classList.add("running");
    function next() {
      if (!playing) return;
      animateTo(stageIndex, function () {
        stageIndex++;
        if (stageIndex >= model.flow.length) { playing = false; setPlayLabel(); return; }
        setTimeout(next, 260);
      });
    }
    next();
  }
  function pause() { playing = false; stopMotion(); setPlayLabel(); }

  if (playBtn) playBtn.addEventListener("click", function () {
    if (playing) { pause(); return; }
    if (stageIndex >= model.flow.length) { clearHighlights(); stageIndex = 0; }
    play();
  });
  if (stepBtn) stepBtn.addEventListener("click", function () {
    pause();
    if (stageIndex >= model.flow.length) { clearHighlights(); stageIndex = 0; }
    animateTo(stageIndex, function () { stageIndex++; });
  });

  // ---- Drill-down drawer + router ----
  var closeTimer = null;
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function renderDrawer(part) {
    clearTimeout(closeTimer);
    var h = [];
    h.push('<button class="close" aria-label="Close">×</button>');
    h.push('<span class="kind-badge">' + esc(part.kind) + "</span>");
    h.push("<h2>" + esc(part.name) + "</h2>");
    h.push('<p class="tagline">' + esc(part.tagline) + "</p>");
    h.push("<p>" + esc(part.description) + "</p>");
    h.push('<div class="enforced"><strong>Enforced by</strong><ul>');
    part.sources.forEach(function (s) { h.push("<li><code>" + esc(s) + "</code></li>"); });
    part.adrs.forEach(function (slug) {
      h.push('<li><a href="adr/' + esc(slug) + '.html">ADR ' + esc(slug) + "</a></li>");
    });
    h.push("</ul></div>");
    part.adrs.forEach(function (slug) {
      if (adrHtml[slug]) h.push('<div class="adr">' + adrHtml[slug] + "</div>");
    });
    drawer.innerHTML = h.join("\n");
    drawer.hidden = false;
    requestAnimationFrame(function () { drawer.classList.add("open"); });
    drawer.querySelector(".close").addEventListener("click", function () { location.hash = "#/"; });
  }

  function openPart(id) {
    var part = partsById[id];
    if (!part) { location.hash = "#/"; return; }
    pause();
    clearHighlights();
    var g = partGroup(id);
    if (g) g.classList.add("active");
    renderDrawer(part);
  }
  function closeDrawer() {
    drawer.classList.remove("open");
    clearTimeout(closeTimer);
    closeTimer = setTimeout(function () { drawer.hidden = true; drawer.innerHTML = ""; }, 280);
  }

  function route() {
    var m = /^#\/part\/(.+)$/.exec(location.hash);
    if (m) openPart(decodeURIComponent(m[1]));
    else closeDrawer();
  }
  window.addEventListener("hashchange", route);

  svg.querySelectorAll(".part").forEach(function (g) {
    var id = g.getAttribute("data-part-id");
    g.addEventListener("click", function () { location.hash = "#/part/" + id; });
    g.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); location.hash = "#/part/" + id; }
    });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && !drawer.hidden) location.hash = "#/";
  });

  // Initial paint: deep-link honored, else auto-play once.
  if (/^#\/part\//.test(location.hash)) { route(); }
  else if (reduce) { highlightStage(model.flow.length - 1); stageIndex = model.flow.length; }
  else { play(); }
})();
