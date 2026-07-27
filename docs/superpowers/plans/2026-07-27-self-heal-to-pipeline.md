# Self-Heal To Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a harness failure become a proposed fix with no human in the middle, by filing the healer's issue into the pipeline's own ingestion label and bounding the resulting loop to one hop.

**Architecture:** Two halves that must land in a specific order. The *code* half adds a second recursion guard to `FailedTasksCheck`: a failed task whose `data["body"]` carries the `<!-- harness-issue:… -->` marker is a fix attempt for an issue the harness itself filed, and is settled to `healed/` without producing a new `Observation`. The *config* half, applied to the operator's live root afterwards, adds `allowed_labels: ["harness:todo"]` to the `file-issue` binding and has the `heal` persona emit that label on its draft, so the existing `harness-todo` process ingests the filed issue. The binding's `label` stays `harness:self-heal` — it is the idempotency search scope, and the ingester deletes `harness:todo` on claim. Landing the config half first is a regression against invariant 25 — see Global Constraints.

**Tech Stack:** Python 3, pytest, hexagonal ports/drivers layout (`src/harness/{ports,drivers,behaviors}`), python-semantic-release on push to `main`, launchd service `com.harness`.

Spec: [docs/superpowers/specs/2026-07-27-self-heal-to-pipeline-design.md](../specs/2026-07-27-self-heal-to-pipeline-design.md)

## Global Constraints

- **Ordering is load-bearing.** Task 5 (the label flip on the live root) must not be applied until Task 1's brake is running in the installed service. The routing change without the brake creates an unbounded issue→fix→failure→issue loop.
- **The brake is not inert until Task 5 lands — it takes effect the moment the release installs.** Today's manual workflow (an operator relabelling a self-heal issue `harness:todo` by hand to route it into the pipeline) already produces a task whose body carries the marker, so from this release such a task failing is settled `heal-declined` instead of healed, with no config change required. This is strictly fail-safe (fewer autonomous actions, never more), so it is not a blocker to this plan's ordering — but it means the two halves are not "no effect / full effect," they are "narrower effect / broader effect."
- **Commit straight into `main`.** This repo's convention (`CLAUDE.md` §Git conventions) — no branch, no PR, don't ask.
- **Conventional commits are load-bearing.** `feat:` bumps the minor, `fix:` the patch, `docs:`/`chore:`/`test:` cut no release. A release is *required* for Task 5, so Task 1 must commit as `feat:`.
- **Do not change the shipped default label** in `src/harness/cli.py`'s `heal.json` template (currently `"label": "harness:self-heal"`, around line 171). Auto-fixing is the operator's opt-in via their own root's workflow file, not a default every `harness init` inherits.
- **Full suite must stay green:** `PYTHONPATH=src .venv/bin/python -m pytest -q` from `~/harness_v2`. `tests/test_architecture.py` in particular — the brake adds a driver→driver import, which is permitted; a driver→core or core→driver import is not.
- The dev checkout is `~/harness_v2`. The live state root is `~/harness-root`. `~/harness-app` is only a worktree base — never edit code there.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/harness/drivers/github_issues.py` | Opens self-heal issues; owns the marker's spelling | Extract `MARKER_PREFIX` so the brake need not re-derive it |
| `src/harness/drivers/failed_tasks_check.py` | Drains `failed/`; owns the recursion guards | Add the one-hop brake |
| `tests/test_failed_tasks_check.py` | Behavioural coverage of the check | Four new tests |
| `CLAUDE.md` | Invariants and gotchas | Amend invariant 25 + the `failed-tasks` gotcha |
| `src/harness/cli.py` | Ships the default personas | Retune `_HEALER_PERSONA` to draft a work order |
| `~/harness-root/agents/heal.json` | Live persona | Mirror the retuned prompt |
| `~/harness-root/workflows/heal.json` | Live heal workflow | `label` → `harness:todo` |

---

### Task 1: The one-hop brake

**Files:**
- Modify: `src/harness/drivers/github_issues.py:18-20`
- Modify: `src/harness/drivers/failed_tasks_check.py` (imports; `evaluate`; a new module-level helper)
- Test: `tests/test_failed_tasks_check.py`

**Interfaces:**
- Consumes: `MemoryTaskQueue`, `MemoryEventSink`, `FakeClock` from `harness.drivers.memory`; the existing `failed_task(...)` and `make_check(...)` helpers at the top of `tests/test_failed_tasks_check.py`.
- Produces: `harness.drivers.github_issues.MARKER_PREFIX: str` (the literal `"<!-- harness-issue:"`, extracted from the existing `marker_comment`), and a settle note containing the substring `heal-declined` on any failed task whose `data["body"]` carries that prefix.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_failed_tasks_check.py`:

```python
def test_marker_prefix_is_the_opening_of_a_rendered_marker():
    """The brake matches on MARKER_PREFIX; this pins it to the real marker's
    spelling, so changing `marker_comment` can never silently disarm it."""
    from harness.drivers.github_issues import MARKER_PREFIX, marker_comment

    assert marker_comment("tsk_boom").startswith(MARKER_PREFIX)


def test_one_hop_brake_declines_a_failed_self_heal_fix():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(
        failed_task(
            "tsk_fix_1",
            data={
                "request": "Fix the driver contract",
                "body": "## Symptom\nboom\n\n<!-- harness-issue:tsk_boom:ab12cd34 -->\n",
            },
        )
    )
    check = make_check(failed=failed, healed=healed)

    observations = check.evaluate()

    assert observations == []
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1
    assert settled[0].status == HEALED
    assert "heal-declined" in settled[0].history[-1].summary


def test_an_unmarked_issue_task_is_still_healed():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(
        failed_task(
            "tsk_plain",
            data={"request": "Add a feature", "body": "## Context\nno marker here\n"},
        )
    )
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.state_key == "tsk_plain"
    assert "queued for healing" in healed.list()[0].history[-1].summary


def test_the_two_recursion_guards_record_distinct_notes():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task("tsk_heal_1", data={"heal": {"of": "tsk_boom"}}))
    failed.put(
        failed_task("tsk_fix_1", data={"body": "<!-- harness-issue:tsk_boom:ab12cd34 -->"})
    )
    check = make_check(failed=failed, healed=healed)

    assert check.evaluate() == []

    notes = {task.id: task.history[-1].summary for task in healed.list()}
    assert "heal-failed" in notes["tsk_heal_1"]
    assert "heal-declined" in notes["tsk_fix_1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/harness_v2 && PYTHONPATH=src .venv/bin/python -m pytest tests/test_failed_tasks_check.py -q`

Expected: FAIL — `ImportError: cannot import name 'MARKER_PREFIX'` on the first new test, and `assert observations == []` failing (one observation produced) on the brake tests.

- [ ] **Step 3: Export the marker prefix**

In `src/harness/drivers/github_issues.py`, replace the existing `marker_comment` definition (lines 18-20):

```python
MARKER_PREFIX = "<!-- harness-issue:"
"""The opening of the hidden idempotency marker. Exported so the one-hop brake
(`failed_tasks_check`) can recognise *any* marker without re-deriving its
spelling — one source for the literal, two readers. The spelling has already
changed once (it was `harness-heal:` before `open-issue` became generic); a
second copy of it elsewhere would have silently disarmed the brake."""


def marker_comment(marker: str) -> str:
    """The hidden idempotency marker embedded in an opened issue's body."""
    return f"{MARKER_PREFIX}{marker} -->"
```

Verify nothing else pinned the old literal: `grep -rn "harness-heal" src/ tests/` should return no hits after this edit.

- [ ] **Step 4: Add the brake**

In `src/harness/drivers/failed_tasks_check.py`, add to the imports (alongside the existing `harness.drivers` / `harness.ports` imports):

```python
from harness.drivers.github_issues import MARKER_PREFIX
```

In `evaluate()`, insert a second guard immediately after the existing `data.heal` guard's `continue`, so the method reads:

```python
            if task.data.get("heal") is not None:
                # This claimed task is itself a `heal`-workflow task that
                # failed. Settle it, never re-observe it — the recursion guard.
                self._settle(task, "heal-failed: the heal attempt itself failed")
                continue
            if _descends_from_a_harness_filed_issue(task):
                # A fix task born from an issue the harness itself filed, which
                # then failed. Healing it again would file a fresh issue and
                # feed the pipeline its own output — the one-hop limit
                # (invariant 25).
                self._settle(
                    task,
                    "heal-declined: fix attempt for a harness-filed issue "
                    "failed (one-hop limit)",
                )
                continue
            self._settle(task, "queued for healing")
            observations.append(self._observation(task))
```

Add the helper at module level, next to the other `_`-prefixed helpers at the bottom of the file:

```python
def _descends_from_a_harness_filed_issue(task: Task) -> bool:
    """True when this failed task is a fix attempt for an issue the harness
    itself filed.

    `OpenIssueBehavior` embeds `<!-- harness-issue:<marker> -->` in every issue
    body it opens, and `GithubIssuesCheck` ingests that body verbatim into
    `data["body"]` — so the marker is provenance that survives the round trip
    through GitHub with no extra plumbing.

    The marker is deliberately generic: it covers the healer and every other
    `open-issue` consumer. That breadth is the point — the rule is that the
    harness does not heal a failure of work it filed for itself, which is the
    same runaway shape wherever it appears, and it is the cycle `data.heal`
    does not cover.
    """
    body = task.data.get("body")
    return isinstance(body, str) and MARKER_PREFIX in body
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ~/harness_v2 && PYTHONPATH=src .venv/bin/python -m pytest tests/test_failed_tasks_check.py -q`

Expected: PASS, 14 passed.

- [ ] **Step 6: Run the full suite**

Run: `cd ~/harness_v2 && PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: PASS with no failures. If `tests/test_architecture.py` fails, the new import is in the wrong place — `failed_tasks_check.py` is a driver and may import a sibling driver; nothing in `dispatcher.py`/`consumer.py` may gain an import from this change.

- [ ] **Step 7: Commit**

```bash
cd ~/harness_v2 && git add src/harness/drivers/github_issues.py src/harness/drivers/failed_tasks_check.py tests/test_failed_tasks_check.py && git commit -m "feat: bound self-healing to one hop per root failure

A fix task born from a healer-filed issue carries no data.heal, so once the
healer files into an ingested label its own output can fail back into failed/
and file a fresh issue, unbounded. Decline any failed task whose body carries
the healer's marker, settling it to healed/ with a distinct note.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Amend invariant 25 and its gotcha

**Files:**
- Modify: `CLAUDE.md` (invariant 25 in §Invariants; the `failed-tasks` bullet in §Gotchas)

**Interfaces:**
- Consumes: the note strings and helper name from Task 1 (`heal-declined`, `MARKER_PREFIX`, `_descends_from_a_harness_filed_issue`).
- Produces: nothing code-facing. Documentation only.

Invariant 25 currently asserts recursion "is guarded by a marker (`data.heal`), not by construction" — after Task 1 there are two guards and two markers, and after Task 5 the second one is what makes the design safe. Leaving it unamended leaves the file asserting something narrower than the code does.

- [ ] **Step 1: Extend invariant 25**

In `CLAUDE.md`, find invariant 25 (begins "**The check produces at most one fresh task per claimed failure…**"). Insert this sentence immediately before its closing "See ADR-0018 and ADR-0019.":

```markdown
**Two markers guard two distinct cycles.** `data.heal` stops a failed *heal*
task from being healed. Once a filed issue also carries an ingested label
(`harness:todo`) rather than only one a human reads, a *fix* task born from
that issue can fail too — and it carries no `data.heal` — so the check
additionally declines any failed task whose `data["body"]` holds the
`<!-- harness-issue:… -->` marker (`MARKER_PREFIX`, exported from
`drivers/github_issues.py` so the literal has one source and two readers; its
spelling has already changed once). It settles to `healed/` with a distinct
`heal-declined` note and yields no `Observation`. The rule is deliberately
broader than self-heal: **the harness does not heal a failure of work it filed
for itself**, which covers every `open-issue` consumer, not just the healer.
One automated fix attempt per root failure; a failed fix waits for the
operator. Both guards settle rather than skip, so `failed/` still drains
monotonically.
```

- [ ] **Step 2: Extend the gotcha**

In `CLAUDE.md` §Gotchas, find the bullet beginning "**The `failed-tasks` check drains `failed/` monotonically; recursion is guarded by a marker, not by construction.**" Append to the end of that bullet:

```markdown
A *second* marker guards the second cycle: a failed task whose `data["body"]`
carries `<!-- harness-issue:… -->` is a fix attempt for an issue the harness
itself filed, and is settled with a `heal-declined` note instead of being healed
again. So a `heal-declined` in `healed/` means "the automated fix didn't work,
it's yours now" — not an error.
```

- [ ] **Step 3: Verify no code changed**

Run: `cd ~/harness_v2 && git diff --stat`

Expected: only `CLAUDE.md` listed.

- [ ] **Step 4: Commit**

```bash
cd ~/harness_v2 && git add CLAUDE.md && git commit -m "docs: record the second recursion guard in invariant 25

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Retune the shipped `heal` persona

**Files:**
- Modify: `src/harness/cli.py:508-545` (`_HEALER_PERSONA`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the prompt text Task 5 mirrors into `~/harness-root/agents/heal.json`. The two must match, so Task 5 copies from here rather than re-authoring.

The persona's output changes audience: it stops being a diagnosis a human reads before deciding, and becomes the input to the `plan` step, which turns it into numbered requirements. **No unit test is added for this step** — asserting prompt prose against a copy of itself tests nothing, and this repo has no persona-content tests to follow. Verification is the full suite (which covers `harness init` writing the file) plus reading the rendered output in Step 3.

- [ ] **Step 1: Replace the drafts-block instruction**

In `src/harness/cli.py`, inside `_HEALER_PERSONA`, replace this fragment:

```python
    '[{"title": "<concise title>", "body": "<diagnosis, then a concrete '
    'proposed change>"}]\n'
```

with:

```python
    '[{"title": "<concise title>", "labels": ["harness:todo"], '
    '"body": "<the work order — see below>"}]\n'
```

The `labels` entry is what routes the issue into the development pipeline: the
`file-issue` binding's `allowed_labels` permits exactly `harness:todo`, and
without it the issue is filed but never ingested — the design's one silent
failure mode. Verify against the current fragment before editing; if the shipped
string already differs from what is quoted here, report DONE_WITH_CONCERNS
rather than forcing a match.

- [ ] **Step 2: Add the work-order specification**

In the same string, immediately after the sentence ending "…Then finish with the outcome that files it.\n\n", insert:

```python
    "The issue you draft is consumed by an automated development pipeline, "
    "not read by a person before work starts — its first step turns your body "
    "into numbered requirements with acceptance criteria. So write the body "
    "as a work order, with these sections:\n"
    "- **Symptom** — what failed, quoting the exact error.\n"
    "- **Reproduction** — the sequence that produces it, or plainly why it "
    "cannot be reproduced on demand.\n"
    "- **Proposed change** — the concrete change, naming files and functions "
    "wherever you can.\n"
    "- **Acceptance criteria** — what must be true for the fix to be done, "
    "stated testably.\n\n"
    "State in the body which kind of finding this is. A code defect is scoped "
    "as a code change; an operational/tuning finding must be scoped as a "
    "configuration change and never as a refactor.\n\n"
```

Leave the existing "recommend diagnostically rather than prescriptively" sentence in place — an automated pipeline inventing a timeout number is precisely what it prevents.

- [ ] **Step 3: Verify the rendered persona**

Run:

```bash
cd ~/harness_v2 && PYTHONPATH=src .venv/bin/python -c "
from harness.cli import _HEALER_PERSONA as p
print(p)
print('---')
for needed in ('Symptom', 'Reproduction', 'Proposed change', 'Acceptance criteria',
               'harness:todo', 'diagnostically rather than prescriptively',
               'never open one'):
    assert needed in p, needed
print('all sections present')
"
```

Expected: the full prompt, then `all sections present`. Read it once end to end — it must still forbid the persona from opening an issue itself and from running or fixing code.

- [ ] **Step 4: Run the full suite**

Run: `cd ~/harness_v2 && PYTHONPATH=src .venv/bin/python -m pytest -q`

Expected: PASS. A failure here is most likely a test asserting on `harness init`'s written persona content.

- [ ] **Step 5: Commit**

```bash
cd ~/harness_v2 && git add src/harness/cli.py && git commit -m "feat: draft self-heal issues as work orders

The healer's issue is becoming the input to the development pipeline's plan
step rather than something a person reads first, so specify the body's shape:
symptom, reproduction, proposed change, acceptance criteria, and which kind of
finding it is.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Smoke-check against the real root, then push

**Files:** none modified. This task gates the release.

**Interfaces:**
- Consumes: the committed state of Tasks 1-3.
- Produces: a published release carrying the brake, which Task 5 requires to be installed before it runs.

Pushing to `main` publishes a release, and `com.harness.autoupdate` installs it within 30 minutes unattended. The precondition from the spec §7 is that the new code starts against the real root's configuration.

- [ ] **Step 1: Smoke-check startup against a copy of the live root**

```bash
cd ~/harness_v2 && cp -R ~/harness-root /tmp/harness-root-smoke && GITHUB_TOKEN=dummy PYTHONPATH=src .venv/bin/python -c "
import harness.cli as cli
async def fake(*a, **k): return None
cli.serve = fake
print('exit:', cli.main(['run','--root','/tmp/harness-root-smoke','--api-port','0','--no-github-source']))
"
```

Expected: `exit: 0`. `--api-port 0` and the stubbed `serve` are required — a real run binds 8420, which the live service holds, and a "pass" caused by a second server dying on a taken port is a failure mode this repo has shipped before. A non-zero exit means stop and diagnose; do not push.

- [ ] **Step 2: Clean up the smoke root**

```bash
rm -rf /tmp/harness-root-smoke
```

- [ ] **Step 3: Push to main**

```bash
cd ~/harness_v2 && git push origin HEAD:main
```

Expected: the push succeeds and GitHub Actions runs Release. If the push is rejected by a required status check, see the `harness-v2-release-required-check` note — the `test` check must not be a required check on `main`.

- [ ] **Step 4: Confirm the release published**

```bash
gh run list --repo onpaj/harness_v2 --workflow Release --limit 3
```

Expected: the newest run is `completed`/`success`. Two `feat:` commits are in this push, so a minor bump (1.1.0 → 1.2.0) is expected.

---

### Task 5: Flip the live root onto the pipeline

**Files:**
- Modify: `~/harness-root/agents/heal.json` (the `prompt` field)
- Modify: `~/harness-root/workflows/heal.json` (`finishers.file-issue.label`)

**Interfaces:**
- Consumes: the persona text from Task 3 and the installed brake from Tasks 1/4.
- Produces: the live behaviour change. Nothing downstream depends on it.

**This task must not start until the brake is running in the installed service.** Applying the label flip against a build without the brake creates the unbounded loop the whole design exists to prevent.

- [ ] **Step 1: Verify the installed service carries the brake**

```bash
~/.local/bin/harness --version && ~/.local/bin/harness update --restart
```

Expected: a version at or above the release from Task 4. If it still reports the older version, `harness update --restart` installs it; re-run `--version` and only continue once it has moved.

Then confirm the *installed* code — not the dev checkout — actually carries the guard:

```bash
grep -c "heal-declined" ~/.local/share/uv/tools/harness/lib/python*/site-packages/harness/drivers/failed_tasks_check.py
```

Expected: `4` (one mention each in the module docstring's two guard descriptions, plus one in each of the two settle-note strings — the whole-branch review widened the brake to a third decline case, adding a second settle note and doc mentions). A `0` or "No such file" means the upgrade did not land; do not proceed to Step 4.

- [ ] **Step 2: Mirror the retuned persona**

Copy the exact prompt from Task 3 into `~/harness-root/agents/heal.json`'s `prompt` field, preserving the file's other keys (`model: "opus"`, `allowed_tools: ["Read", "Write"]`, `allowed_outcomes`, `timeout`). Generate it rather than retyping:

```bash
cd ~/harness_v2 && PYTHONPATH=src .venv/bin/python -c "
import json, pathlib
from harness.cli import _HEALER_PERSONA
p = pathlib.Path.home() / 'harness-root/agents/heal.json'
spec = json.loads(p.read_text())
spec['prompt'] = _HEALER_PERSONA
p.write_text(json.dumps(spec, indent=2) + '\n')
print('prompt updated,', len(_HEALER_PERSONA), 'chars')
"
```

- [ ] **Step 3: Verify the persona file still parses as a spec**

```bash
cd ~/harness_v2 && PYTHONPATH=src .venv/bin/python -c "
from harness.drivers.fs_agents import FilesystemAgentCatalog
import pathlib
cat = FilesystemAgentCatalog(pathlib.Path.home() / 'harness-root/agents')
spec = cat.get('heal')
assert 'Acceptance criteria' in spec.prompt
print('ok:', spec.model, spec.allowed_tools)
"
```

Expected: `ok: opus ['Read', 'Write']`. A load error here means the JSON write broke the file — restore it from `~/nanoclaw-backup` or re-run Step 2 before continuing.

- [ ] **Step 4: Allow the routing label**

Edit `~/harness-root/workflows/heal.json`, in `finishers.file-issue`. **Leave `label` as `harness:self-heal`** — it is the idempotency search scope, and the ingesting process deletes `harness:todo` on claim, so scoping to it would break duplicate suppression. Add one key:

```json
    "file-issue": {
      "kind": "open-issue",
      "from_step": "heal",
      "label": "harness:self-heal",
      "allowed_labels": ["harness:todo"]
    }
```

- [ ] **Step 5: Verify the workflow still compiles**

```bash
cd ~/harness_v2 && PYTHONPATH=src .venv/bin/python -c "
from harness.drivers.fs_workflows import FilesystemWorkflowRepository
import pathlib
repo = FilesystemWorkflowRepository(pathlib.Path.home() / 'harness-root/workflows')
wf = repo.get('heal')
b = wf.finishers['file-issue']
print('binding:', b)
assert b.config.get('label') == 'harness:self-heal', b.config
assert 'harness:todo' in b.config.get('allowed_labels', ()), b.config
print('ok')
"
```

Expected: the binding prints, then `ok`. A raise here means the file is malformed — fix before restarting, since a bad workflow file crash-loops the service.

- [ ] **Step 6: Restart the service**

```bash
launchctl kickstart -k gui/$(id -u)/com.harness
```

- [ ] **Step 7: Confirm the service came back**

```bash
sleep 5; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8420/ && tail -20 ~/harness-root/logs/harness.error.log
```

Expected: `200`, and no new `workflow 'heal' does not exist` or parse errors in the log tail.

---

### Task 6: End-to-end verification on the live system

**Files:** none. This is the acceptance test for the whole plan.

**Interfaces:**
- Consumes: everything above, deployed.
- Produces: the evidence that the loop closes and that it stops after one hop.

- [ ] **Step 1: Force a failure**

Submit a task naming a repository that is not in `repos.json`, so it fails when the first step resolves its worktree:

```bash
~/.local/bin/harness submit --root ~/harness-root --workflow development \
  --repo does-not-exist \
  --data '{"request":"deliberate failure: verifying the self-heal loop","title":"deliberate failure: self-heal loop check"}'
```

(`--repo`, not `--repository`; the request travels inside `--data`.)

- [ ] **Step 2: Watch it reach `failed/` and then `healed/`**

```bash
ls ~/harness-root/failed/ ~/harness-root/healed/
```

Expected: within ~30s the task appears in `healed/` with a `queued for healing` note, and a `heal` task is running on the board.

- [ ] **Step 3: Confirm the issue is filed into the pipeline**

```bash
gh issue list --repo onpaj/harness_v2 --state open --limit 10 --json number,title,labels --jq '.[] | "\(.number) \([.labels[].name]|join(",")) \(.title)"'
```

Expected: a new issue carrying **both** `harness:todo` and `harness:self-heal` — or, within one 30s poll, `harness:queued` in place of `harness:todo`, meaning the ingestion process already claimed it.

If the healer judged the failure external/transient and skipped, no issue is filed and that is correct behaviour, not a bug — repeat Step 1 with a failure that looks like a harness defect, or read the `heal` artifact to see the verdict.

- [ ] **Step 4: Confirm the fix task started**

```bash
curl -s http://127.0.0.1:8420/api/tasks | python3 -c "
import json,sys
for t in json.load(sys.stdin).get('tasks', []):
    print(t.get('id'), t.get('status'), str(t.get('data',{}).get('title'))[:60])
"
```

Expected: a task in the `development` workflow whose title is the healer's issue title.

- [ ] **Step 5: Verify the brake — the assertion that matters**

This is the negative test, and the one the whole design rests on. Rather than waiting for the real fix task to fail, inject a synthetic failed task shaped exactly like one — a `data["body"]` carrying the marker, which is the only thing the brake reads. Record the issue count first:

```bash
BEFORE=$(gh issue list --repo onpaj/harness_v2 --state open --label harness:self-heal --json number --jq 'length'); echo "before: $BEFORE"
```

Inject:

```bash
python3 - <<'PY'
import json, pathlib
root = pathlib.Path.home() / "harness-root"
task = {
    "id": "tsk_brake_probe",
    "repository": "harness_v2",
    "workflowTemplate": "development",
    "step": None,
    "status": "failed",
    "lastOutcome": None,
    "lockId": None,
    "created": "2026-07-27T00:00:00Z",
    "data": {
        "title": "synthetic: brake probe",
        "body": "## Symptom\nsynthetic\n\n<!-- harness-issue:tsk_synthetic:ab12cd34 -->\n",
    },
    "history": [
        {"at": "2026-07-27T00:00:00Z", "actor": "consumer:development",
         "from": "development", "to": "failed", "reason": "synthetic brake probe"}
    ],
}
(root / "failed" / "tsk_brake_probe.json").write_text(json.dumps(task, indent=1))
print("injected")
PY
```

Wait one poll interval, then assert both halves:

```bash
sleep 45
ls ~/harness-root/failed/ ~/harness-root/healed/tsk_brake_probe.json
grep -o "heal-declined[^\"]*" ~/harness-root/healed/tsk_brake_probe.json
gh issue list --repo onpaj/harness_v2 --state open --label harness:self-heal --json number --jq 'length'
```

Expected: `failed/` is empty of the probe; `healed/tsk_brake_probe.json` exists and its last history entry contains `heal-declined: fix attempt for a harness-filed issue failed (one-hop limit)`; and the issue count is **unchanged from `$BEFORE`**. A count that grew means the brake did not fire — stop and revert Task 5 Step 4 (label back to `harness:self-heal`) before investigating.

- [ ] **Step 5b: Remove the probe**

```bash
rm -f ~/harness-root/healed/tsk_brake_probe.json
```

- [ ] **Step 6: Clean up the deliberate failure**

Close the issue opened in Step 3 and delete any leftover worktree for the test tasks if one was created. Do not merge the PR if the pipeline opened one — it is a fix for a synthetic failure.

---

## Deferred (not in this plan)

From spec §6, each needing its own spec: **stuck-task detection** (a task oscillating `verify → development` never reaches `failed/`) and **service liveness** (a crash-looping service produces no task, and a harness Process cannot watch the harness). From the conversation: **auto-merge**, which the operator is implementing separately.
