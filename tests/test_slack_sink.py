"""`SlackWebhookSink` — task progress/outcome → webhook messages.

The outbound-only mirror of `GithubLabelReflector`, routed on the destination
identity (`task.data.sink.kind`), not the origin. The HTTP POST is injected as
a recording fake — no network anywhere; the default stdlib helper is covered
by monkeypatching `urllib` at the boundary.
"""

from __future__ import annotations

import json

from harness.drivers.slack_sink import SlackWebhookSink, post_json
from harness.models import Task
from harness.ports.source import FinishResult, Progress

URL = "https://hooks.slack.example/T0/B0/secret"


class RecordingPost:
    """Records every `(url, payload)` the sink posts."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def __call__(self, url: str, payload: dict) -> None:
        self.posts.append((url, payload))


def _sink() -> tuple[SlackWebhookSink, RecordingPost]:
    post = RecordingPost()
    return SlackWebhookSink(webhook_url=URL, post=post), post


def _task(task_id: str = "t-1", **data) -> Task:
    return Task(
        id=task_id,
        created="2026-07-23T10:00:00Z",
        data={"sink": {"kind": "slack"}, **data},
    )


def test_poll_is_always_empty() -> None:
    sink, _ = _sink()
    assert sink.poll() == []


def test_foreign_task_without_sink_is_noop() -> None:
    sink, post = _sink()
    task = Task(id="t-1", created="2026-07-23T10:00:00Z")

    sink.report_progress(task, Progress(step="development"))
    sink.finish(task, FinishResult(ok=True))

    assert post.posts == []


def test_foreign_task_with_other_sink_kind_is_noop() -> None:
    sink, post = _sink()
    task = Task(
        id="t-1",
        created="2026-07-23T10:00:00Z",
        data={"sink": {"kind": "github"}},
    )

    sink.report_progress(task, Progress(step="development"))
    sink.finish(task, FinishResult(ok=False))

    assert post.posts == []


def test_report_progress_posts_task_id_and_step() -> None:
    sink, post = _sink()

    sink.report_progress(_task(), Progress(step="development"))

    [(url, payload)] = post.posts
    assert url == URL
    assert "t-1" in payload["text"]
    assert "development" in payload["text"]


def test_report_progress_includes_the_title_when_present() -> None:
    sink, post = _sink()

    sink.report_progress(_task(title="Fix bug"), Progress(step="development"))

    [(_, payload)] = post.posts
    assert "Fix bug" in payload["text"]


def test_repeated_report_progress_same_step_posts_once() -> None:
    sink, post = _sink()

    sink.report_progress(_task(), Progress(step="development"))
    sink.report_progress(_task(), Progress(step="development"))

    assert len(post.posts) == 1


def test_report_progress_different_step_posts_again() -> None:
    sink, post = _sink()

    sink.report_progress(_task(), Progress(step="development"))
    sink.report_progress(_task(), Progress(step="review"))

    assert len(post.posts) == 2
    assert "review" in post.posts[1][1]["text"]


def test_finish_ok_and_failed_produce_distinct_texts() -> None:
    sink_ok, post_ok = _sink()
    sink_ok.finish(_task(), FinishResult(ok=True))

    sink_failed, post_failed = _sink()
    sink_failed.finish(_task(), FinishResult(ok=False, summary="the build broke"))

    [(_, ok_payload)] = post_ok.posts
    [(_, failed_payload)] = post_failed.posts
    assert ok_payload["text"] != failed_payload["text"]
    assert "failed" in failed_payload["text"]
    assert "the build broke" in failed_payload["text"]


def test_repeated_finish_posts_once() -> None:
    sink, post = _sink()

    sink.finish(_task(), FinishResult(ok=True))
    sink.finish(_task(), FinishResult(ok=True))

    assert len(post.posts) == 1


def test_default_post_helper_sends_a_json_post(monkeypatch) -> None:
    """The stdlib helper, monkeypatched at the `urllib` boundary — no network."""
    import urllib.request

    opened = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda request: opened.append(request))

    post_json(URL, {"text": "hello"})

    [request] = opened
    assert request.full_url == URL
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data.decode("utf-8")) == {"text": "hello"}
