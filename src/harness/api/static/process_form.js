// Disables the repository checkbox list while "All repositories" is selected.
// The form still works with JS disabled — both radios submit meaningfully,
// the server is the actual source of truth for what "scope" means.
(function () {
  var field = document.getElementById("repositories-field");
  if (!field) { return; }
  var radios = field.querySelectorAll('input[name="scope"]');
  var checkboxes = field.querySelectorAll('input[name="repositories"]');

  function sync() {
    var allSelected = field.querySelector('input[name="scope"][value="all"]').checked;
    checkboxes.forEach(function (box) { box.disabled = allSelected; });
  }

  radios.forEach(function (radio) { radio.addEventListener("change", sync); });
  sync();
})();
