"""The merge verdict parser: `harness.merge_verdict`.

Every failure mode here must be fail-safe — an unreadable verdict has to end
as "no merge", never as "merge with a default confidence".
"""

from __future__ import annotations

import pytest

from harness.merge_verdict import MergeVerdict, VerdictError, parse_verdict


def test_parses_a_well_formed_verdict():
    verdict = parse_verdict(
        "The change is small and well tested.\n\n"
        '```json\n{"confidence": 0.91, "reasoning": "one-line fix, covered", '
        '"risks": ["touches the retry path"]}\n```\n'
    )
    assert verdict == MergeVerdict(
        confidence=0.91,
        reasoning="one-line fix, covered",
        risks=("touches the retry path",),
    )


def test_reasoning_and_risks_are_optional():
    verdict = parse_verdict('```json\n{"confidence": 0.5}\n```')
    assert verdict.confidence == 0.5
    assert verdict.reasoning == ""
    assert verdict.risks == ()


def test_the_last_fenced_block_wins():
    """An example earlier in the review must not be mistaken for the verdict —
    the same rule `parse_drafts` and `_extract_verdict` already follow."""
    verdict = parse_verdict(
        'For example one might write:\n```json\n{"confidence": 1.0}\n```\n'
        'My actual verdict:\n```json\n{"confidence": 0.2}\n```'
    )
    assert verdict.confidence == 0.2


def test_an_empty_artifact_is_an_error_not_a_zero():
    """Unlike `parse_drafts`, there is no legitimate "wrote nothing" path here:
    the step only reaches the finisher when the review already approved."""
    with pytest.raises(VerdictError, match="no artifact"):
        parse_verdict("   \n  ")


def test_a_report_without_a_json_block_is_an_error():
    with pytest.raises(VerdictError, match="no fenced json block"):
        parse_verdict("I reviewed it and it looks fine to me.")


def test_malformed_json_is_an_error():
    with pytest.raises(VerdictError, match="not valid JSON"):
        parse_verdict('```json\n{"confidence": 0.9,,}\n```')


def test_an_array_is_an_error():
    with pytest.raises(VerdictError, match="must be a JSON object"):
        parse_verdict('```json\n[{"confidence": 0.9}]\n```')


def test_a_missing_confidence_is_an_error():
    with pytest.raises(VerdictError, match="no numeric 'confidence'"):
        parse_verdict('```json\n{"reasoning": "looks good"}\n```')


def test_a_string_confidence_is_an_error():
    with pytest.raises(VerdictError, match="no numeric 'confidence'"):
        parse_verdict('```json\n{"confidence": "high"}\n```')


def test_a_boolean_confidence_is_an_error():
    """`true` is an int subclass in Python — reading it as 1.0 would silently
    mean maximum confidence, the single worst way to misread this field."""
    with pytest.raises(VerdictError, match="no numeric 'confidence'"):
        parse_verdict('```json\n{"confidence": true}\n```')


@pytest.mark.parametrize(
    ("written", "expected"), [(1.5, 1.0), (-0.2, 0.0), (1, 1.0), (0, 0.0)]
)
def test_confidence_is_clamped_not_rejected(written, expected):
    """A persona writing 1.5 meant "very confident", not "bypass the gate"."""
    verdict = parse_verdict(f'```json\n{{"confidence": {written}}}\n```')
    assert verdict.confidence == expected


def test_a_non_string_reasoning_is_an_error():
    with pytest.raises(VerdictError, match="non-string 'reasoning'"):
        parse_verdict('```json\n{"confidence": 0.9, "reasoning": 42}\n```')


def test_non_array_risks_is_an_error():
    with pytest.raises(VerdictError, match="non-array 'risks'"):
        parse_verdict('```json\n{"confidence": 0.9, "risks": "none"}\n```')
