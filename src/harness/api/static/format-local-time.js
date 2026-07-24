(function () {
  // Expects datetime attributes in the harness's on-disk UTC ISO-8601 shape
  // (%Y-%m-%dT%H:%M:%SZ). A future format change would silently stop
  // rendering here — the try/catch below leaves the raw fallback text in
  // place rather than breaking the page.
  function localizeTimes(root) {
    var nodes = (root || document).querySelectorAll('time[datetime]');
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      try {
        var date = new Date(node.getAttribute('datetime'));
        if (isNaN(date.getTime())) { continue; }
        node.textContent = new Intl.DateTimeFormat(undefined, {
          dateStyle: 'medium',
          timeStyle: 'medium',
          timeZoneName: 'short',
        }).format(date);
      } catch (error) {
        // Leave the server-rendered raw-UTC fallback text in place.
      }
    }
  }
  window.localizeTimes = localizeTimes;
})();
