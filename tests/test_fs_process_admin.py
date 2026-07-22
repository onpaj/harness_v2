"""`FilesystemProcessAdmin`: the write-side editor over the same
`processes/*.json` files `FilesystemProcessRepository` compiles.

Mirrors `test_fs_agent_admin.py`. The round-trips prove `write` then `read`
returns equal fields *and* that the file it leaves compiles cleanly through the
repository; the validation cases prove `write` maps a `compile_process` failure
to the right form field and never leaves a partial file behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.drivers.fs_processes import (
    FilesystemProcessAdmin,
    FilesystemProcessRepository,
)
from harness.drivers.system_clock import SystemClock
from harness.ports.process_admin import (
    ProcessAdminValidationError,
    ProcessFields,
    ProcessNotFound,
)


def _compiles(root: Path) -> None:
    """The file the admin wrote must compile through the runtime repository."""
    FilesystemProcessRepository(root).build(clock=SystemClock())


# --- round trips ------------------------------------------------------------


def test_always_workflow_round_trips(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    written = admin.write(
        "nightly",
        ProcessFields(
            interval="1h", check="always", target_kind="workflow", target="wf"
        ),
    )

    assert written == ProcessFields(
        interval="1h",
        check="always",
        target_kind="workflow",
        target="wf",
        params={},
        sink_kind="none",
        dedup="per-interval",
    )
    assert admin.read("nightly") == written
    _compiles(tmp_path)


def test_disk_threshold_step_round_trips(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    written = admin.write(
        "disk-pressure",
        ProcessFields(
            interval="30m",
            check="disk-threshold",
            target_kind="step",
            target="cleanup",
            params={"path": "/", "percent": 80},
            dedup="per-state",
        ),
    )

    assert written == ProcessFields(
        interval="30m",
        check="disk-threshold",
        target_kind="step",
        target="cleanup",
        params={"path": "/", "percent": 80},
        sink_kind="none",
        dedup="per-state",
    )
    assert admin.read("disk-pressure") == written
    _compiles(tmp_path)


# --- validation: right field key --------------------------------------------


def _valid(**overrides) -> ProcessFields:
    base = dict(
        interval="1h", check="always", target_kind="workflow", target="wf"
    )
    base.update(overrides)
    return ProcessFields(**base)


@pytest.mark.parametrize(
    "fields, field",
    [
        (_valid(interval="1x"), "interval"),
        (_valid(check="nope"), "check"),
        (
            ProcessFields(
                interval="1h",
                check="disk-threshold",
                target_kind="step",
                target="cleanup",
            ),
            "params",
        ),
        (_valid(dedup="per-eternity"), "dedup"),
        (_valid(sink_kind="slack"), "sink"),
        (_valid(target_kind="banana"), "target"),
    ],
)
def test_write_maps_a_compile_failure_to_the_right_field(
    tmp_path: Path, fields: ProcessFields, field: str
) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    with pytest.raises(ProcessAdminValidationError) as excinfo:
        admin.write("bad", fields)

    assert field in excinfo.value.errors
    assert not (tmp_path / "bad.json").exists()


def test_write_invalid_name_is_rejected(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    with pytest.raises(ProcessAdminValidationError) as excinfo:
        admin.write("../secret", _valid())

    assert "name" in excinfo.value.errors


def test_write_rejected_submission_leaves_existing_file_untouched(
    tmp_path: Path,
) -> None:
    admin = FilesystemProcessAdmin(tmp_path)
    admin.write("nightly", _valid(interval="1h"))

    with pytest.raises(ProcessAdminValidationError):
        admin.write("nightly", _valid(interval="1x"))

    assert admin.read("nightly").interval == "1h"


# --- read / delete / list ---------------------------------------------------


def test_read_missing_raises_not_found(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    with pytest.raises(ProcessNotFound):
        admin.read("missing")


def test_read_invalid_name_raises_not_found(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    with pytest.raises(ProcessNotFound):
        admin.read("../secret")


def test_read_malformed_file_raises_not_found(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    admin = FilesystemProcessAdmin(tmp_path)

    with pytest.raises(ProcessNotFound):
        admin.read("broken")


def test_delete_reports_true_then_false_and_updates_list(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)
    admin.write("nightly", _valid())

    assert admin.list() == ("nightly",)
    assert admin.delete("nightly") is True
    assert admin.delete("nightly") is False
    assert admin.list() == ()


# --- option lists (through the port) ----------------------------------------


def test_check_names_and_sink_kinds(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    assert "always" in admin.check_names()
    assert "disk-threshold" in admin.check_names()
    assert admin.sink_kinds() == ("none",)
