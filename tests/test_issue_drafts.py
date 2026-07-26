"""`issue_drafts` — the fenced-JSON draft contract, parsed."""

import pytest

from harness.issue_drafts import DraftError, IssueDraft, marker_for, parse_drafts

ARTIFACT = """# Architecture Review: Analytics

Some prose the human reads.

```json
[
  {"title": "Analytics: handler does too much", "body": "## Finding\\n...", "labels": ["tech-debt"]},
  {"title": "Analytics: DTO is a record", "body": "breaks client generation"}
]
```
"""


def test_parses_every_draft_in_the_last_fenced_block():
    drafts = parse_drafts(ARTIFACT)

    assert drafts == [
        IssueDraft(
            title="Analytics: handler does too much",
            body="## Finding\n...",
            labels=("tech-debt",),
        ),
        IssueDraft(
            title="Analytics: DTO is a record",
            body="breaks client generation",
            labels=(),
        ),
    ]


def test_an_empty_array_is_a_valid_zero_issue_report():
    assert parse_drafts("All clean.\n\n```json\n[]\n```\n") == []


def test_an_empty_artifact_is_zero_drafts_not_an_error():
    """The step wrote no file at all — heal's `skip` path."""
    assert parse_drafts("") == []
    assert parse_drafts("   \n") == []


def test_a_report_with_no_fenced_block_is_an_error():
    with pytest.raises(DraftError, match="no fenced json block"):
        parse_drafts("# A report with no machine-readable block\n")


def test_a_block_that_is_not_an_array_is_an_error():
    with pytest.raises(DraftError, match="must be a JSON array"):
        parse_drafts('```json\n{"title": "not an array"}\n```')


def test_broken_json_is_an_error():
    with pytest.raises(DraftError, match="is not valid JSON"):
        parse_drafts("```json\n[{,}]\n```")


def test_a_draft_without_a_title_is_an_error():
    with pytest.raises(DraftError, match="draft 1 has no title"):
        parse_drafts('```json\n[{"title": "ok"}, {"body": "no title"}]\n```')


def test_the_last_block_wins():
    """An agent that showed an example earlier in its report must not confuse us."""
    artifact = (
        '```json\n[{"title": "an example, not a finding"}]\n```\n'
        "\nActual findings:\n\n"
        '```json\n[{"title": "the real one"}]\n```\n'
    )

    assert [d.title for d in parse_drafts(artifact)] == ["the real one"]


def test_a_draft_with_no_body_still_succeeds_with_the_empty_default():
    drafts = parse_drafts('```json\n[{"title": "no body field at all"}]\n```')

    assert drafts == [IssueDraft(title="no body field at all", body="", labels=())]


def test_a_draft_with_a_wrongly_typed_body_is_an_error():
    with pytest.raises(DraftError, match="draft 0 has a non-string body"):
        parse_drafts('```json\n[{"title": "ok", "body": 5}]\n```')


def test_a_draft_with_wrongly_typed_labels_is_an_error():
    with pytest.raises(DraftError, match="draft 0 has non-array labels"):
        parse_drafts('```json\n[{"title": "ok", "labels": "oops"}]\n```')


def test_the_marker_is_task_scoped_and_title_content_scoped():
    first = marker_for("tsk_abc", "A finding")
    again = marker_for("tsk_abc", "A finding")
    other_title = marker_for("tsk_abc", "A different finding")
    other_task = marker_for("tsk_xyz", "A finding")

    assert first == again
    assert first.startswith("tsk_abc:")
    assert first != other_title
    assert first != other_task
