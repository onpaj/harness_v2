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


def test_a_report_with_no_fenced_block_is_zero_drafts_not_an_error():
    """A clean review that concludes in prose, with no ```json block at all,
    is 'nothing to file' — same bucket as an empty artifact or an explicit
    `[]`, not a malformed-output error."""
    assert parse_drafts("# A report with no machine-readable block\n") == []


def test_an_uppercase_opener_tag_is_matched():
    assert parse_drafts('```JSON\n[{"title": "ok"}]\n```') == [
        IssueDraft(title="ok", body="", labels=())
    ]


def test_a_mixed_case_opener_tag_is_matched():
    assert parse_drafts('```Json\n[{"title": "ok"}]\n```') == [
        IssueDraft(title="ok", body="", labels=())
    ]


def test_a_fence_indented_or_followed_by_blank_lines_still_parses():
    artifact = '  ```json\n  [{"title": "ok"}]\n  ```\n\n\n'

    assert [d.title for d in parse_drafts(artifact)] == ["ok"]


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


# The shape that broke self-healing in production: the healer quotes the
# failure it is reporting, so the draft's own body carries a fenced block.
# Those backticks live *inside* a JSON string, but a non-greedy regex closing
# on the first ``` cut the array there and left the body string unterminated.
ARTIFACT_WITH_FENCED_BODY = (
    "# Heal report\n\nProse the human reads.\n\n"
    "```json\n"
    '[{"title": "Agent step exceeds its timeout", "labels": ["harness:todo"], '
    '"body": "## Symptom\\n\\nThe task failed with:\\n\\n'
    "```\\nbehavior raised an exception: claude timed out after 1800.0s\\n```"
    '\\n\\n## Fix\\n\\nRaise the budget."}]\n'
    "```\n"
)


def test_a_draft_body_may_contain_a_fenced_code_block():
    drafts = parse_drafts(ARTIFACT_WITH_FENCED_BODY)

    assert len(drafts) == 1
    assert drafts[0].title == "Agent step exceeds its timeout"
    assert "```\nbehavior raised an exception" in drafts[0].body
    assert drafts[0].body.endswith("Raise the budget.")


def test_trailing_prose_after_the_block_is_not_part_of_the_json():
    """The JSON value ends where JSON says it ends — anything after it in the
    artifact is the human's text, not a parse error."""
    drafts = parse_drafts('```json\n[{"title": "ok"}]\n```\n\nThanks for reading.\n')

    assert [d.title for d in drafts] == ["ok"]
