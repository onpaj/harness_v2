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

  // ---- Port catalogue (index only) ----
  // The catalogue is plain server-rendered <details> elements, so it works
  // fully without JS. This enhancement only keeps the URL hash in sync with
  // the open port (deep-linkable) and honors a deep link on load.
  var ports = document.querySelectorAll("details.port");
  if (!ports.length) return; // doc page: theme only

  ports.forEach(function (port) {
    port.addEventListener("toggle", function () {
      if (port.open) {
        history.replaceState(null, "", "#" + port.id);
      } else if (location.hash === "#" + port.id) {
        history.replaceState(null, "", location.pathname);
      }
    });
  });

  function openFromHash() {
    var id = location.hash.replace(/^#/, "");
    if (!id) return;
    var target = document.getElementById(id);
    if (!target) return;
    // A deep link may point at a driver inside a closed port.
    var port = target.closest ? target.closest("details.port") : null;
    if (port) port.open = true;
    if (target !== port && target.tagName === "DETAILS") target.open = true;
    target.scrollIntoView({ block: "start" });
  }
  window.addEventListener("hashchange", openFromHash);
  openFromHash();
})();
