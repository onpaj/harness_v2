(function () {
  // Expects datetime attributes in the harness's on-disk UTC ISO-8601 shape
  // (%Y-%m-%dT%H:%M:%SZ). A future format change would silently stop
  // rendering here — the try/catch below leaves the raw fallback text in
  // place rather than breaking the page.
  //
  // dateStyle/timeStyle must never be combined with individual component
  // options (like the timezone-name one) — ECMA-402 makes that combination
  // a TypeError, and the swallowing try/catch would then leave every
  // timestamp on the page in raw UTC. timeStyle 'long' already carries the
  // short timezone name.
  var formatter = null;
  try {
    formatter = new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'long',
    });
  } catch (error) {
    // No Intl support — every time keeps its server-rendered UTC fallback.
  }

  function localizeTimes(root) {
    if (!formatter) { return; }
    var nodes = (root || document).querySelectorAll('time[datetime]');
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      try {
        var date = new Date(node.getAttribute('datetime'));
        if (isNaN(date.getTime())) { continue; }
        node.textContent = formatter.format(date);
      } catch (error) {
        // Leave the server-rendered raw-UTC fallback text in place.
      }
    }
  }
  window.localizeTimes = localizeTimes;
})();
