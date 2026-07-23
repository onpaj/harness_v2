from harness.api.routes import _shorttime


def test_formats_a_z_suffixed_utc_timestamp():
    assert _shorttime("2026-07-19T10:00:05Z") == "Jul 19, 10:00"


def test_formats_a_timestamp_with_explicit_offset():
    assert _shorttime("2026-07-19T10:00:05+00:00") == "Jul 19, 10:00"


def test_passes_through_none_unchanged():
    assert _shorttime(None) == ""


def test_passes_through_empty_string_unchanged():
    assert _shorttime("") == ""


def test_passes_through_unparseable_text_unchanged():
    assert _shorttime("not-a-timestamp") == "not-a-timestamp"
