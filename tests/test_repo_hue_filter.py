import zlib

from harness.api.routes import _repo_hue


def test_passes_through_none_as_empty_string():
    assert _repo_hue(None) == ""


def test_passes_through_empty_string_unchanged():
    assert _repo_hue("") == ""


def test_derives_a_hue_in_range_deterministically():
    hue = _repo_hue("app-backend")
    assert hue == str(zlib.crc32(b"app-backend") % 360)
    assert 0 <= int(hue) < 360


def test_same_name_always_yields_the_same_hue():
    assert _repo_hue("my-repo") == _repo_hue("my-repo")


def test_different_names_yield_different_hues():
    # Not a mathematical guarantee for arbitrary strings, but true for these
    # two real repo names and demonstrates the derivation isn't a constant.
    assert _repo_hue("app-backend") != _repo_hue("my-repo")


def test_uses_the_full_registry_name_not_a_basename():
    # A path-shaped repository value and its basename are different strings,
    # so they must hash to different hues — the filter is fed the raw
    # `task.repository`, not `basename(task.repository)` (design-01.md, "1").
    assert _repo_hue("/Users/x/repos/my-repo") != _repo_hue("my-repo")
