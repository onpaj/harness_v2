"""`FilesystemProcessAdmin`: the write-side editor over the same
`processes/*.json` files `FilesystemProcessRepository` compiles.

Mirrors `test_fs_agent_admin.py`. The round-trips prove `write` then `read`
returns equal fields *and* that the file it leaves compiles cleanly through the
repository; the validation cases prove `write` maps a `compile_process` failure
to the right form field and never leaves a partial file behind.
"""

from __future__ import annotations

import json
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


def test_cron_cadence_round_trips(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    written = admin.write(
        "weekly-review",
        ProcessFields(
            cadence="cron",
            cron="0 6 * * 1",
            check="always",
            target_kind="workflow",
            target="wf",
        ),
    )

    assert written == ProcessFields(
        cadence="cron",
        cron="0 6 * * 1",
        interval="",
        check="always",
        target_kind="workflow",
        target="wf",
        params={},
        sink_kind="none",
        dedup="per-interval",
    )
    assert admin.read("weekly-review") == written
    _compiles(tmp_path)


def test_slack_sink_round_trips(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    written = admin.write(
        "notify",
        ProcessFields(
            interval="1h",
            check="always",
            target_kind="workflow",
            target="wf",
            sink_kind="slack",
        ),
    )

    assert written.sink_kind == "slack"
    assert admin.read("notify") == written
    _compiles(tmp_path)


def test_github_sink_round_trips(tmp_path: Path) -> None:
    admin = FilesystemProcessAdmin(tmp_path)

    written = admin.write(
        "notify-github",
        ProcessFields(
            interval="1h",
            check="always",
            target_kind="workflow",
            target="wf",
            sink_kind="github",
        ),
    )

    assert written.sink_kind == "github"
    assert admin.read("notify-github") == written
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
        (_valid(sink_kind="teams"), "sink"),
        (_valid(target_kind="banana"), "target"),
        (_valid(cadence="cron", cron="0 6 31 2 *"), "cron"),
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


def test_cron_cadence_with_blank_box_maps_to_cron_not_interval(tmp_path: Path) -> None:
    # The explicit `cadence` discriminator, not "whichever field is non-blank":
    # toggling to cron but leaving the box empty must report against
    # `errors.cron`, never `errors.interval`.
    admin = FilesystemProcessAdmin(tmp_path)

    with pytest.raises(ProcessAdminValidationError) as excinfo:
        admin.write("bad", _valid(cadence="cron", cron=""))

    assert "cron" in excinfo.value.errors
    assert "interval" not in excinfo.value.errors


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


def test_read_tolerates_trigger_kind_and_write_never_emits_it(tmp_path: Path) -> None:
    # `trigger.kind` is a read-tolerated reservation, not an editable field:
    # a file carrying `"kind": "schedule"` reads fine, and a re-write through
    # the admin drops the key (the form never emits it).
    (tmp_path / "kinded.json").write_text(
        json.dumps(
            {
                "trigger": {"kind": "schedule", "interval": "1h"},
                "action": {"check": "always"},
                "target": {"workflow": "wf"},
            }
        ),
        encoding="utf-8",
    )
    admin = FilesystemProcessAdmin(tmp_path)

    fields = admin.read("kinded")
    assert fields.interval == "1h"

    admin.write("kinded", fields)
    raw = json.loads((tmp_path / "kinded.json").read_text(encoding="utf-8"))
    assert "kind" not in raw["trigger"]


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
    assert admin.sink_kinds() == ("github", "none", "slack")


# --- the wired registry (checks beyond the built-ins) ------------------------


def test_check_names_reflects_the_wired_registry(tmp_path: Path) -> None:
    from harness.drivers.checks import BUILTIN_CHECKS, AlwaysCheck

    admin = FilesystemProcessAdmin(
        tmp_path,
        checks={**BUILTIN_CHECKS, "github-issues": lambda params: AlwaysCheck()},
    )

    assert "github-issues" in admin.check_names()
    assert "always" in admin.check_names()


def test_write_validates_against_the_wired_registry(tmp_path: Path) -> None:
    """A check the wiring registered beyond the built-ins (the GitHub-backed
    actions, `failed-tasks`) is writable through the admin — the same registry
    drives the dropdown and the save-time compile, so the dashboard accepts
    exactly what the runtime would. The default (built-ins-only) admin still
    rejects the same name."""
    from harness.drivers.checks import BUILTIN_CHECKS, AlwaysCheck

    admin = FilesystemProcessAdmin(
        tmp_path,
        checks={**BUILTIN_CHECKS, "github-issues": lambda params: AlwaysCheck()},
    )

    written = admin.write("ingest", _valid(check="github-issues"))

    assert written.check == "github-issues"
    assert admin.read("ingest") == written

    with pytest.raises(ProcessAdminValidationError) as excinfo:
        FilesystemProcessAdmin(tmp_path / "plain").write(
            "ingest", _valid(check="github-issues")
        )
    assert "check" in excinfo.value.errors


def test_write_maps_a_wired_factory_failure_onto_its_field(tmp_path: Path) -> None:
    """A wired factory that raises `ProcessValidationError` itself (the
    no-`GITHUB_TOKEN` github factories do, with `field="check"`) surfaces as
    that form-field error — never a crash, never a written file."""
    from harness.drivers.checks import BUILTIN_CHECKS
    from harness.drivers.fs_processes import ProcessValidationError

    def factory(params):
        raise ProcessValidationError(
            "github-issues action requires GITHUB_TOKEN", field="check"
        )

    admin = FilesystemProcessAdmin(
        tmp_path, checks={**BUILTIN_CHECKS, "github-issues": factory}
    )

    with pytest.raises(ProcessAdminValidationError) as excinfo:
        admin.write("ingest", _valid(check="github-issues"))

    assert "check" in excinfo.value.errors
    assert not (tmp_path / "ingest.json").exists()
