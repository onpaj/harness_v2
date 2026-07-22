# Architecture Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the plain Markdown drill-down site with an interactive, animated Architecture Explorer whose home page is a hexagonal ports-and-adapters diagram a task token travels, and where clicking a part drills into the full ADR that grounds it.

**Architecture:** A hand-authored architecture *model* (Python dataclasses) is the single source of truth for the diagram; a `validate()` guard — run in tests — keeps it coherent with the real ADRs and source tree. The generator (`site.py`) emits a self-contained static site: an `index.html` carrying the model + per-part ADR HTML as embedded JSON and an SVG diagram, plus restyled per-document pages, plus copied `app.css`/`app.js` assets. All interactivity (token animation, drill-down drawer, hash routing, theme toggle) is client-side vanilla JS over the embedded data.

**Tech Stack:** Python 3.11 (stdlib only), hand-authored HTML/CSS/vanilla-JS output, SVG for the diagram, pytest for the generator tests. No new dependencies, no framework, no build toolchain, no runtime CDN.

## Global Constraints

- **Python 3.11+, standard library only.** No new runtime or dev dependencies. (`pyproject.toml` pins the toolchain; do not add to it.)
- **Output is a fully self-contained static site.** Zero external URLs in generated HTML/CSS/JS (no CDN scripts, fonts, styles, or images). A test asserts this.
- **`scripts/build_docs.py` stays the entry point** with its existing `--out` flag (default `site`). Do not change its interface; it is wired into `.github/workflows/pages.yml`.
- **The generator lives in `src/harness_docs_site/` and imports nothing from the `harness` package** (it is a standalone tool; see `src/harness_docs_site/__init__.py`).
- **Reuse `markdown.py` and `corpus.py` as-is** for Markdown→HTML and document discovery. Do not reimplement Markdown rendering.
- **Model-validation tests go in a NEW file `tests/test_architecture_model.py`.** Do NOT touch `tests/test_architecture.py` — that file tests the `harness` package's own import invariants, unrelated to the docs site.
- **Run the suite with:** `cd <repo> && uv run pytest -q` (or `pytest -q` inside the project venv). Individual test: `uv run pytest tests/test_architecture_model.py::test_name -v`.
- **ADR slugs are the file stem** of `docs/adr/<slug>.md` (e.g. `0001-ports-and-adapters`). Source paths are repo-relative (e.g. `src/harness/ports/source.py`).

---

## File Structure

- **Create** `src/harness_docs_site/architecture.py` — `Part`, `Stage`, `Edge`, `ArchitectureModel` dataclasses; `MODEL`; `validate()`; `model_to_dict()`.
- **Create** `src/harness_docs_site/assets/app.css` — theme (dark default + light variant), hexagon, drawer, doc typography, reduced-motion.
- **Create** `src/harness_docs_site/assets/app.js` — hash router, token animation controller, drawer renderer, theme toggle, keyboard handling.
- **Rewrite** `src/harness_docs_site/site.py` — emit explorer `index.html` (embedded JSON + SVG), restyled doc pages under a shared shell, and copy the `assets/` dir.
- **Reuse** `src/harness_docs_site/corpus.py`, `src/harness_docs_site/markdown.py` — unchanged.
- **Create** `tests/test_architecture_model.py` — model + `validate()` tests, incl. real-tree coherence.
- **Extend** `tests/test_docs_site.py` — build-smoke tests for the new output contract.
- **Unchanged** `scripts/build_docs.py`, `.github/workflows/pages.yml`.

---

## Task 1: Architecture model dataclasses and `validate()`

**Files:**
- Create: `src/harness_docs_site/architecture.py`
- Test: `tests/test_architecture_model.py`

**Interfaces:**
- Consumes: nothing (leaf module, stdlib only).
- Produces:
  - `Part(id: str, name: str, kind: str, tagline: str, description: str, adrs: tuple[str, ...], sources: tuple[str, ...], x: float, y: float, related_docs: tuple[str, ...] = ())`
  - `Stage(part_id: str, caption: str)`
  - `Edge(src: str, dst: str)`
  - `ArchitectureModel(parts: tuple[Part, ...], flow: tuple[Stage, ...], edges: tuple[Edge, ...])`
  - `validate(model: ArchitectureModel, repo_root: Path) -> None` — raises `ValueError` on any incoherence.
  - `model_to_dict(model: ArchitectureModel) -> dict` — JSON-ready plain dict.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_architecture_model.py`:

```python
from pathlib import Path

import pytest

from harness_docs_site.architecture import (
    ArchitectureModel,
    Edge,
    Part,
    Stage,
    model_to_dict,
    validate,
)


def _tree(root: Path) -> Path:
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "docs" / "adr" / "0001-ports-and-adapters.md").write_text(
        "# ADR-0001: Ports and adapters\n", encoding="utf-8"
    )
    (root / "src").mkdir()
    (root / "src" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _part(**kw) -> Part:
    base = dict(
        id="a",
        name="A",
        kind="core",
        tagline="t",
        description="d",
        adrs=("0001-ports-and-adapters",),
        sources=("src/thing.py",),
        x=50.0,
        y=50.0,
    )
    base.update(kw)
    return Part(**base)


def test_validate_passes_on_a_coherent_model(tmp_path: Path):
    _tree(tmp_path)
    b = _part(id="b", x=10.0, y=10.0)
    model = ArchitectureModel(
        parts=(_part(id="a"), b),
        flow=(Stage("a", "start"), Stage("b", "end")),
        edges=(Edge("a", "b"),),
    )
    validate(model, tmp_path)  # must not raise


def test_validate_rejects_unknown_adr_slug(tmp_path: Path):
    _tree(tmp_path)
    model = ArchitectureModel(
        parts=(_part(adrs=("9999-nope",)),),
        flow=(Stage("a", "only"),),
        edges=(),
    )
    with pytest.raises(ValueError, match="9999-nope"):
        validate(model, tmp_path)


def test_validate_rejects_missing_source_path(tmp_path: Path):
    _tree(tmp_path)
    model = ArchitectureModel(
        parts=(_part(sources=("src/gone.py",)),),
        flow=(Stage("a", "only"),),
        edges=(),
    )
    with pytest.raises(ValueError, match="src/gone.py"):
        validate(model, tmp_path)


def test_validate_rejects_dangling_flow_stage(tmp_path: Path):
    _tree(tmp_path)
    model = ArchitectureModel(
        parts=(_part(id="a"),),
        flow=(Stage("ghost", "only"),),
        edges=(),
    )
    with pytest.raises(ValueError, match="ghost"):
        validate(model, tmp_path)


def test_validate_rejects_dangling_edge_endpoint(tmp_path: Path):
    _tree(tmp_path)
    model = ArchitectureModel(
        parts=(_part(id="a"),),
        flow=(Stage("a", "only"),),
        edges=(Edge("a", "ghost"),),
    )
    with pytest.raises(ValueError, match="ghost"):
        validate(model, tmp_path)


def test_validate_rejects_orphan_part(tmp_path: Path):
    _tree(tmp_path)
    # 'b' is on no edge and not in the flow -> orphan.
    model = ArchitectureModel(
        parts=(_part(id="a"), _part(id="b", x=1.0, y=1.0)),
        flow=(Stage("a", "only"),),
        edges=(),
    )
    with pytest.raises(ValueError, match="orphan"):
        validate(model, tmp_path)


def test_model_to_dict_is_json_shaped(tmp_path: Path):
    part = _part()
    model = ArchitectureModel(parts=(part,), flow=(Stage("a", "s"),), edges=())
    data = model_to_dict(model)
    assert data["parts"][0]["id"] == "a"
    assert data["parts"][0]["adrs"] == ["0001-ports-and-adapters"]
    assert data["flow"][0] == {"part_id": "a", "caption": "s"}
    assert isinstance(data["parts"][0]["x"], float)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_architecture_model.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness_docs_site.architecture'`.

- [ ] **Step 3: Write `architecture.py` (dataclasses, `validate`, `model_to_dict`) — leave `MODEL` for Task 2**

Create `src/harness_docs_site/architecture.py`:

```python
"""The curated architecture model the Explorer draws — the single source of
truth for the diagram, its animated flow, and every part's drill-down.

Hand-authored (not derived from the live module graph): a small, legible graph
of the harness's parts, each grounded in the ADR(s) that decide it. `validate`
keeps this model honest against the real `docs/adr/` files and `src/` tree, and
runs in the test suite so a rename that breaks the mapping fails CI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

_KINDS = {"port", "driver", "core", "ui", "store"}


@dataclass(frozen=True)
class Part:
    id: str
    name: str
    kind: str  # one of _KINDS
    tagline: str
    description: str
    adrs: tuple[str, ...]  # ADR slugs: docs/adr/<slug>.md
    sources: tuple[str, ...]  # repo-relative source paths
    x: float  # 0..100 diagram coordinate
    y: float  # 0..100 diagram coordinate
    related_docs: tuple[str, ...] = ()  # optional extra doc slugs (specs/plans)


@dataclass(frozen=True)
class Stage:
    part_id: str
    caption: str


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str


@dataclass(frozen=True)
class ArchitectureModel:
    parts: tuple[Part, ...]
    flow: tuple[Stage, ...]
    edges: tuple[Edge, ...]


def validate(model: ArchitectureModel, repo_root: Path) -> None:
    """Raise ValueError if the model is internally inconsistent or has drifted
    from the real docs/source tree. Silence means coherent."""
    repo_root = Path(repo_root)
    ids = [p.id for p in model.parts]
    id_set = set(ids)

    if len(ids) != len(id_set):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate part id(s): {dupes}")

    for part in model.parts:
        if part.kind not in _KINDS:
            raise ValueError(f"part {part.id!r} has unknown kind {part.kind!r}")
        for slug in part.adrs:
            if not (repo_root / "docs" / "adr" / f"{slug}.md").is_file():
                raise ValueError(
                    f"part {part.id!r} cites missing ADR {slug!r}"
                )
        for src in part.sources:
            if not (repo_root / src).exists():
                raise ValueError(
                    f"part {part.id!r} cites missing source path {src!r}"
                )

    for stage in model.flow:
        if stage.part_id not in id_set:
            raise ValueError(f"flow stage references unknown part {stage.part_id!r}")

    referenced: set[str] = set()
    for edge in model.edges:
        for endpoint in (edge.src, edge.dst):
            if endpoint not in id_set:
                raise ValueError(f"edge references unknown part {endpoint!r}")
            referenced.add(endpoint)
    referenced.update(stage.part_id for stage in model.flow)

    orphans = sorted(id_set - referenced)
    if orphans:
        raise ValueError(f"orphan part(s) on no edge and not in flow: {orphans}")


def model_to_dict(model: ArchitectureModel) -> dict:
    """Plain, JSON-serializable dict for embedding in the page."""
    return asdict(model)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_architecture_model.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/harness_docs_site/architecture.py tests/test_architecture_model.py
git commit -m "feat(docs-site): architecture model dataclasses and validate()"
```

---

## Task 2: The concrete `MODEL` and its real-tree coherence test

**Files:**
- Modify: `src/harness_docs_site/architecture.py` (append the `MODEL` instance)
- Test: `tests/test_architecture_model.py` (add the coherence test)

**Interfaces:**
- Consumes: `Part`, `Stage`, `Edge`, `ArchitectureModel`, `validate` from Task 1.
- Produces: `MODEL: ArchitectureModel` — the shipped 11-part model.

**Context — diagram layout.** Coordinates are on a `0..100` × `0..100` grid used later as the SVG viewBox. Left column = external/GitHub boundary and stores; centre = the pure core; the flow reads left→right. Ports carry `kind="port"`, filesystem/git/github adapters `kind="driver"`, decision logic `kind="core"`, the web board `kind="ui"`, on-disk state `kind="store"`.

- [ ] **Step 1: Write the failing coherence test**

Append to `tests/test_architecture_model.py`:

```python
def test_shipped_model_is_coherent_with_the_repo():
    from harness_docs_site.architecture import MODEL

    repo_root = Path(__file__).resolve().parents[1]
    validate(MODEL, repo_root)  # must not raise against the real tree


def test_shipped_model_flow_starts_at_the_task_source():
    from harness_docs_site.architecture import MODEL

    assert MODEL.flow[0].part_id == "task-source"
    part_ids = {p.id for p in MODEL.parts}
    assert {"router", "agent-runner", "landing", "board"} <= part_ids
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_architecture_model.py::test_shipped_model_is_coherent_with_the_repo -q`
Expected: FAIL — `ImportError: cannot import name 'MODEL'`.

- [ ] **Step 3: Append the `MODEL` to `architecture.py`**

Add at the end of `src/harness_docs_site/architecture.py`:

```python
MODEL = ArchitectureModel(
    parts=(
        Part(
            id="task-source",
            name="TaskSource",
            kind="port",
            tagline="The one seam to the outside world.",
            description=(
                "Every task enters through a single port. The harness core never "
                "knows it is talking to GitHub — it asks TaskSource for work and "
                "gets domain tasks back, so a new origin is a new adapter, not a "
                "change to the loop."
            ),
            adrs=("0010-tasksource-single-external-port",),
            sources=("src/harness/ports/source.py",),
            x=8.0,
            y=30.0,
        ),
        Part(
            id="github-source",
            name="GitHub adapter",
            kind="driver",
            tagline="Turns GitHub issues into tasks; opens PRs back.",
            description=(
                "The adapter behind TaskSource. It polls repositories named in the "
                "registry for labelled issues and hands them to the core, and it is "
                "the same GitHub boundary a finished task's pull request goes out "
                "through."
            ),
            adrs=(
                "0010-tasksource-single-external-port",
                "0008-repository-registry-name-to-path",
            ),
            sources=(
                "src/harness/drivers/github_source.py",
                "src/harness/drivers/github_forge.py",
            ),
            x=8.0,
            y=12.0,
        ),
        Part(
            id="repo-registry",
            name="RepositoryRegistry",
            kind="store",
            tagline="Maps a repo name to a path on this machine.",
            description=(
                "A task carries a repo *name*, never a filesystem path. The registry "
                "resolves the name to a clone on disk, so the same task definition is "
                "portable across machines and the core stays free of absolute paths."
            ),
            adrs=("0008-repository-registry-name-to-path",),
            sources=(
                "src/harness/ports/repos.py",
                "src/harness/drivers/fs_repos.py",
            ),
            x=8.0,
            y=52.0,
        ),
        Part(
            id="queues",
            name="Queues",
            kind="core",
            tagline="Directories; claimed atomically by rename.",
            description=(
                "Each workflow step is a directory. A worker claims a task by "
                "renaming its file into a private processing dir — an atomic, "
                "lock-free operation the filesystem guarantees, so two workers can "
                "never claim the same task."
            ),
            adrs=("0003-atomic-queue-claim-by-rename",),
            sources=(
                "src/harness/ports/queue.py",
                "src/harness/drivers/fs_queue.py",
            ),
            x=30.0,
            y=30.0,
        ),
        Part(
            id="router",
            name="Router",
            kind="core",
            tagline="A pure function: (task, outcome) → next step.",
            description=(
                "The router is the workflow state machine with no side effects. Given "
                "a task and the outcome of a step, it returns the next queue — and "
                "nothing else. It imports only the domain models, which is what makes "
                "the whole workflow trivially testable."
            ),
            adrs=("0004-pure-router",),
            sources=("src/harness/router.py",),
            x=52.0,
            y=30.0,
        ),
        Part(
            id="agent-runner",
            name="Agent runner",
            kind="core",
            tagline="Runs a step's agent; splits decide / act / persist.",
            description=(
                "The consumer/dispatcher that runs a step: it invokes the step's "
                "agent behavior, keeps the LLM's *decision* separate from the *act* "
                "that applies it and the *persist* that records it, and reports the "
                "outcome back to the router."
            ),
            adrs=(
                "0002-three-way-decision-split",
                "0007-agent-persona-as-data",
            ),
            sources=(
                "src/harness/behaviors/agent.py",
                "src/harness/dispatcher.py",
                "src/harness/consumer.py",
            ),
            x=74.0,
            y=30.0,
        ),
        Part(
            id="persona-catalog",
            name="Persona catalog",
            kind="store",
            tagline="Each agent is data, not a subclass.",
            description=(
                "An agent's persona — its prompt, model, and tools — is a JSON record "
                "the catalog serves, not code. Adding or editing an agent never "
                "touches the runner; the behavior is fixed, the persona varies."
            ),
            adrs=("0007-agent-persona-as-data",),
            sources=(
                "src/harness/ports/agent.py",
                "src/harness/drivers/fs_agents.py",
            ),
            x=74.0,
            y=12.0,
        ),
        Part(
            id="worktree",
            name="Worktree",
            kind="driver",
            tagline="Each task gets an isolated git worktree.",
            description=(
                "A task's agent works in its own git worktree branched from the "
                "registered clone, so parallel tasks never collide and the main "
                "checkout is never disturbed."
            ),
            adrs=(
                "0006-worktree-vs-artifact-folder-split",
                "0009-landing-proposes-never-touches-main",
            ),
            sources=(
                "src/harness/ports/workspace.py",
                "src/harness/drivers/git_workspace.py",
            ),
            x=74.0,
            y=52.0,
        ),
        Part(
            id="artifact-folder",
            name="Artifact folder",
            kind="store",
            tagline="Per-stage outputs, kept out of the worktree.",
            description=(
                "Each stage writes its artifacts (plan, review, logs) to a folder "
                "kept separate from the code worktree, so generated notes never end "
                "up committed to the branch under review."
            ),
            adrs=("0006-worktree-vs-artifact-folder-split",),
            sources=(
                "src/harness/ports/artifacts.py",
                "src/harness/drivers/worktree_artifacts.py",
                "src/harness/artifacts_layout.py",
            ),
            x=52.0,
            y=52.0,
        ),
        Part(
            id="landing",
            name="Landing",
            kind="core",
            tagline="Proposes a PR; never touches main.",
            description=(
                "When the work is done, landing pushes the branch and opens a pull "
                "request. It proposes — it never merges or writes to main — so a "
                "human (or a downstream check) always makes the final call."
            ),
            adrs=("0009-landing-proposes-never-touches-main",),
            sources=("src/harness/behaviors/landing.py",),
            x=52.0,
            y=12.0,
        ),
        Part(
            id="board",
            name="Board",
            kind="ui",
            tagline="Read-only web view; writes go through ports.",
            description=(
                "The web board renders task state live. It never imports a driver: it "
                "reads through BoardView and StageOutputView and writes through "
                "TaskControl, so the UI is decoupled from how state is stored."
            ),
            adrs=(
                "0005-ui-never-imports-a-driver",
                "0011-taskcontrol-write-side-of-boardview",
                "0012-stageoutputview-third-ui-surface",
            ),
            sources=(
                "src/harness/api/app.py",
                "src/harness/ports/board.py",
                "src/harness/ports/control.py",
            ),
            x=30.0,
            y=52.0,
        ),
    ),
    flow=(
        Stage("task-source", "A labelled GitHub issue becomes a task"),
        Stage("queues", "Claimed atomically by rename into a step dir"),
        Stage("router", "The pure router picks the next step"),
        Stage("agent-runner", "The step's agent does the work"),
        Stage("worktree", "…in an isolated git worktree"),
        Stage("landing", "Landing opens a PR — never touches main"),
        Stage("github-source", "The pull request goes back out to GitHub"),
    ),
    edges=(
        Edge("github-source", "task-source"),
        Edge("repo-registry", "github-source"),
        Edge("task-source", "queues"),
        Edge("queues", "router"),
        Edge("router", "agent-runner"),
        Edge("agent-runner", "persona-catalog"),
        Edge("agent-runner", "worktree"),
        Edge("agent-runner", "artifact-folder"),
        Edge("agent-runner", "landing"),
        Edge("landing", "github-source"),
        Edge("queues", "board"),
    ),
)
```

- [ ] **Step 4: Run the coherence tests**

Run: `uv run pytest tests/test_architecture_model.py -q`
Expected: PASS (all, including the real-tree coherence test). If it fails on a missing source path or ADR slug, fix the offending `sources`/`adrs` entry to match the real tree — do not weaken `validate`.

- [ ] **Step 5: Commit**

```bash
git add src/harness_docs_site/architecture.py tests/test_architecture_model.py
git commit -m "feat(docs-site): ship the concrete architecture MODEL, coherent with the tree"
```

---

## Task 3: Rewrite `site.py` to emit the explorer, embedded data, SVG, and assets

**Files:**
- Rewrite: `src/harness_docs_site/site.py`
- Create: `src/harness_docs_site/assets/app.css` (empty placeholder this task; real content in Task 4)
- Create: `src/harness_docs_site/assets/app.js` (empty placeholder this task; real content in Task 5)
- Test: `tests/test_docs_site.py` (add new-contract tests; keep existing ones passing)

**Interfaces:**
- Consumes: `discover_docs`/`DocEntry` (`corpus.py`), `render` (`markdown.py`), `MODEL`/`model_to_dict` (`architecture.py`).
- Produces (public surface unchanged in name): `build_site(entries: list[DocEntry], repo_root: Path, out_dir: Path) -> None`. Now also writes `assets/app.css`, `assets/app.js`, embeds `#model-data` and `#adr-html` JSON in `index.html`, and emits an SVG diagram with one `<g class="part" data-part-id="...">` per part.

**Context — the output contract the JS (Tasks 4–5) depends on:**
- `index.html` contains `<script type="application/json" id="model-data">…</script>` (the `model_to_dict(MODEL)` payload) and `<script type="application/json" id="adr-html">…</script>` (a `{adr_slug: rendered_html}` map covering every slug any part cites).
- `index.html` contains an `<svg id="hexmap" viewBox="0 0 100 100">` with, per part, a `<g class="part" data-part-id="<id>" data-kind="<kind>">` positioned at the part's `x,y`, and `<line class="edge">` per edge.
- Every generated page links `assets/app.css`; `index.html` also loads `assets/app.js` (`<script src="assets/app.js" defer>`). Doc pages use paths relative to their `<category>/` subdir (`../assets/app.css`).
- No absolute `http://`/`https://` URL appears anywhere in the output.

- [ ] **Step 1: Create empty asset placeholders**

```bash
mkdir -p src/harness_docs_site/assets
printf '/* app.css — filled in Task 4 */\n' > src/harness_docs_site/assets/app.css
printf '/* app.js — filled in Task 5 */\n' > src/harness_docs_site/assets/app.js
```

- [ ] **Step 2: Write the failing new-contract tests**

Append to `tests/test_docs_site.py`:

```python
import json
import re


def test_build_site_embeds_model_and_adr_html(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    index = (out_dir / "index.html").read_text(encoding="utf-8")
    assert 'id="model-data"' in index
    assert 'id="adr-html"' in index
    assert 'id="hexmap"' in index
    # Every part in the model renders a clickable node.
    from harness_docs_site.architecture import MODEL

    for part in MODEL.parts:
        assert f'data-part-id="{part.id}"' in index


def test_build_site_copies_assets_and_links_them(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    assert (out_dir / "assets" / "app.css").is_file()
    assert (out_dir / "assets" / "app.js").is_file()
    index = (out_dir / "index.html").read_text(encoding="utf-8")
    assert 'href="assets/app.css"' in index
    assert 'src="assets/app.js"' in index
    # Doc pages reference the asset one level up.
    adr_entry = next(e for e in entries if e.category == "adr")
    page = (out_dir / "adr" / adr_entry.output_name).read_text(encoding="utf-8")
    assert 'href="../assets/app.css"' in page


def test_build_site_output_has_no_external_urls(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    entries = discover_docs(tmp_path)
    out_dir = tmp_path / "site"
    build_site(entries, tmp_path, out_dir)

    for path in out_dir.rglob("*"):
        if path.suffix in {".html", ".css", ".js"}:
            text = path.read_text(encoding="utf-8")
            assert "http://" not in text, f"external URL in {path}"
            assert "https://" not in text, f"external URL in {path}"


def test_embedded_model_json_parses_and_matches_model(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r'<script type="application/json" id="model-data">(.*?)</script>',
        index,
        re.DOTALL,
    )
    assert match
    data = json.loads(match.group(1))
    from harness_docs_site.architecture import MODEL

    assert [p["id"] for p in data["parts"]] == [p.id for p in MODEL.parts]
```

Note: the fixture tree's ADRs are named `0001-example-decision.md`; the real `MODEL` cites real slugs like `0010-tasksource-single-external-port`. The `#adr-html` map is built from the *parts' cited slugs*, reading each `docs/adr/<slug>.md` under `repo_root`. In the fixture those files do not exist, so `build_site` must **skip a cited slug whose file is absent** (emit an empty/omitted entry) rather than crash — the embed test only asserts the JSON block exists. The coherence test in Task 2 already guarantees the real slugs exist in the real tree.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_docs_site.py -q`
Expected: FAIL on the new tests (missing `model-data`, assets, etc.).

- [ ] **Step 4: Rewrite `site.py`**

Replace the contents of `src/harness_docs_site/site.py` with:

```python
"""Writes the generated Architecture Explorer: an animated explorer index, one
restyled page per document, and the copied static assets.

Idempotent — `build_site` clears `out_dir` first, so re-running the generator
(or rebuilding after a doc rename) never leaves a stale page behind.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from harness_docs_site.architecture import MODEL, model_to_dict
from harness_docs_site.corpus import DocEntry
from harness_docs_site.markdown import render

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"

_CATEGORY_TITLES = {
    "adr": "Architecture Decision Records",
    "spec": "Phase specs",
    "plan": "Phase plans",
    "project": "Project docs",
}
_CATEGORY_ORDER = ["adr", "spec", "plan", "project"]

_PAGE_SHELL = """<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{css_href}">
</head>
<body class="{body_class}">
{body}
</body>
</html>
"""


def _shell(title: str, body: str, css_href: str, body_class: str) -> str:
    return _PAGE_SHELL.format(
        title=html.escape(title),
        body=body,
        css_href=css_href,
        body_class=body_class,
    )


def _svg_diagram() -> str:
    parts = {p.id: p for p in MODEL.parts}
    out = ['<svg id="hexmap" viewBox="0 0 100 100" role="img" '
           'aria-label="Architecture map">']
    out.append('<g class="edges">')
    for edge in MODEL.edges:
        a, b = parts[edge.src], parts[edge.dst]
        out.append(
            f'<line class="edge" x1="{a.x}" y1="{a.y}" x2="{b.x}" y2="{b.y}" '
            f'data-src="{edge.src}" data-dst="{edge.dst}"></line>'
        )
    out.append("</g>")
    out.append('<g class="parts">')
    for part in MODEL.parts:
        out.append(
            f'<g class="part" data-part-id="{html.escape(part.id)}" '
            f'data-kind="{html.escape(part.kind)}" tabindex="0" role="button" '
            f'aria-label="{html.escape(part.name)}">'
        )
        out.append(f'<circle class="node" cx="{part.x}" cy="{part.y}" r="4.2"></circle>')
        out.append(
            f'<text class="label" x="{part.x}" y="{part.y + 7.5}" '
            f'text-anchor="middle">{html.escape(part.name)}</text>'
        )
        out.append("</g>")
    out.append("</g>")
    out.append('<circle id="token" r="1.8"></circle>')
    out.append("</svg>")
    return "\n".join(out)


def _adr_html_map(repo_root: Path) -> dict[str, str]:
    """Rendered HTML for every ADR any part cites. A cited slug whose file is
    absent (e.g. under a test fixture) is skipped, never fatal."""
    slugs: list[str] = []
    for part in MODEL.parts:
        for slug in part.adrs:
            if slug not in slugs:
                slugs.append(slug)
    mapping: dict[str, str] = {}
    for slug in slugs:
        path = repo_root / "docs" / "adr" / f"{slug}.md"
        if path.is_file():
            mapping[slug] = render(path.read_text(encoding="utf-8"))
    return mapping


def _json_block(element_id: str, payload: object) -> str:
    # `</` cannot appear inside a <script>; escape the slash defensively.
    text = json.dumps(payload).replace("</", "<\\/")
    return f'<script type="application/json" id="{element_id}">{text}</script>'


def _index_page(entries: list[DocEntry], repo_root: Path) -> str:
    by_category: dict[str, list[DocEntry]] = {key: [] for key in _CATEGORY_ORDER}
    for entry in entries:
        by_category.setdefault(entry.category, []).append(entry)

    body = ['<header class="topbar">',
            '<span class="brand">harness</span>',
            '<button id="theme-toggle" type="button" aria-label="Toggle theme">◑</button>',
            '</header>',
            '<main class="explorer">',
            '<section class="stage">',
            '<div class="controls">',
            '<button id="play" type="button">▶ Play</button>',
            '<button id="step" type="button">Step ›</button>',
            '<span id="caption" class="caption"></span>',
            '</div>',
            _svg_diagram(),
            '<ul class="legend">',
            ]
    for kind in ("port", "driver", "core", "ui", "store"):
        body.append(f'<li data-kind="{kind}"><span class="swatch"></span>{kind}</li>')
    body.append("</ul>")
    body.append("</section>")

    body.append('<aside id="drawer" class="drawer" hidden></aside>')
    body.append("</main>")

    body.append('<section class="doc-index">')
    for category in _CATEGORY_ORDER:
        group = sorted(by_category.get(category, []), key=lambda e: e.sort_key)
        if not group:
            continue
        body.append(f"<h2>{html.escape(_CATEGORY_TITLES[category])}</h2>")
        body.append("<ul>")
        for entry in group:
            href = f"{entry.category}/{entry.output_name}"
            body.append(
                f'<li><a href="{html.escape(href)}">{html.escape(entry.title)}</a></li>'
            )
        body.append("</ul>")
    body.append("</section>")

    body.append(_json_block("model-data", model_to_dict(MODEL)))
    body.append(_json_block("adr-html", _adr_html_map(repo_root)))
    body.append('<script src="assets/app.js" defer></script>')

    return _shell("harness — architecture explorer", "\n".join(body),
                  css_href="assets/app.css", body_class="page-explorer")


def _doc_page(entry: DocEntry) -> str:
    text = entry.source_path.read_text(encoding="utf-8")
    body = [
        '<header class="topbar">',
        '<a class="brand" href="../index.html">← harness</a>',
        '<button id="theme-toggle" type="button" aria-label="Toggle theme">◑</button>',
        '</header>',
        '<article class="doc">',
        render(text),
        '</article>',
        '<script src="../assets/app.js" defer></script>',
    ]
    return _shell(entry.title, "\n".join(body),
                  css_href="../assets/app.css", body_class="page-doc")


def build_site(entries: list[DocEntry], repo_root: Path, out_dir: Path) -> None:
    """Write the explorer index, a page per entry under `<category>/`, and the
    copied `assets/` dir. Clears and recreates `out_dir` first."""
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    shutil.copytree(_ASSETS_DIR, out_dir / "assets")

    (out_dir / "index.html").write_text(
        _index_page(entries, repo_root), encoding="utf-8"
    )

    for entry in entries:
        category_dir = out_dir / entry.category
        category_dir.mkdir(parents=True, exist_ok=True)
        (category_dir / entry.output_name).write_text(
            _doc_page(entry), encoding="utf-8"
        )
```

- [ ] **Step 5: Update the pre-existing doc-site tests for the new shell**

Two existing tests assert the old markup. Update them in `tests/test_docs_site.py`:
- `test_build_site_writes_a_page_per_entry_with_title_and_back_link`: the back-link text is now `← harness` to `../index.html`. Change the assertion `assert "index.html" in page` — it still holds (`../index.html`). Keep it.
- No other existing assertion references removed markup (`_index_page` still emits `href="{category}/{output_name}"`, so `test_build_site_writes_index_with_links_to_every_entry` still passes; ADR heading test still passes).

Run the whole doc-site file to confirm nothing else broke:

Run: `uv run pytest tests/test_docs_site.py -q`
Expected: PASS (existing + 4 new tests).

- [ ] **Step 6: Full suite + real build smoke**

Run: `uv run pytest -q`
Expected: PASS (whole suite).

Run: `python scripts/build_docs.py --out /tmp/site-check && ls /tmp/site-check /tmp/site-check/assets`
Expected: `index.html`, `assets/`, `adr/`, `spec/`, `plan/`, `project/` present; `assets/` has `app.css`, `app.js`.

- [ ] **Step 7: Commit**

```bash
git add src/harness_docs_site/site.py src/harness_docs_site/assets tests/test_docs_site.py
git commit -m "feat(docs-site): emit explorer index with embedded model, SVG map, and assets"
```

---

## Task 4: `app.css` — dark/light theme, hexmap, drawer, doc typography

**Files:**
- Rewrite: `src/harness_docs_site/assets/app.css`
- Test: `tests/test_docs_site.py` (one guard test)

**Interfaces:**
- Consumes: the DOM/class contract emitted by `site.py` (`.topbar`, `.explorer`, `#hexmap`, `.part`, `.edge`, `#token`, `.drawer`, `.legend [data-kind]`, `.doc`, `[data-theme]`).
- Produces: styling only. No new IDs/classes the JS depends on beyond what `site.py` already emits.

**Context.** Dark is the default (`<html data-theme="dark">`). A `[data-theme="light"]` override provides the light variant; `app.js` (Task 5) flips the attribute. Colours by `kind` come from CSS custom properties so the legend and nodes stay in sync. Respect `prefers-reduced-motion`.

- [ ] **Step 1: Guard test (asset non-empty + theme hooks present)**

Append to `tests/test_docs_site.py`:

```python
def test_app_css_defines_theme_and_kind_colours(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    css = (tmp_path / "site" / "assets" / "app.css").read_text(encoding="utf-8")
    assert '[data-theme="light"]' in css
    assert "prefers-reduced-motion" in css
    for kind in ("port", "driver", "core", "ui", "store"):
        assert f"--kind-{kind}" in css
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_docs_site.py::test_app_css_defines_theme_and_kind_colours -q`
Expected: FAIL (placeholder CSS lacks the hooks).

- [ ] **Step 3: Write `app.css`**

Replace `src/harness_docs_site/assets/app.css` with:

```css
/* Architecture Explorer — self-contained theme. No external fonts/assets. */
:root {
  --bg: #0b0f17;
  --panel: #121826;
  --ink: #e6edf6;
  --muted: #93a4bd;
  --line: #223049;
  --accent: #38bdf8;
  --glow: #38bdf8;
  --kind-port: #38bdf8;
  --kind-driver: #a78bfa;
  --kind-core: #f59e0b;
  --kind-ui: #34d399;
  --kind-store: #f472b6;
  --font: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
[data-theme="light"] {
  --bg: #f6f8fc;
  --panel: #ffffff;
  --ink: #0b1220;
  --muted: #55637a;
  --line: #d6deec;
  --accent: #0284c7;
  --glow: #0ea5e9;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font);
  line-height: 1.6;
}
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 22px; border-bottom: 1px solid var(--line);
  position: sticky; top: 0; background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: blur(6px); z-index: 5;
}
.brand { font-weight: 700; letter-spacing: .02em; text-decoration: none; color: var(--ink); }
#theme-toggle {
  background: transparent; color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; padding: 4px 10px; cursor: pointer; font-size: 16px;
}

/* Explorer layout */
.explorer { display: grid; grid-template-columns: 1fr; gap: 0; }
.stage { padding: 22px clamp(12px, 4vw, 48px); }
.controls { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; min-height: 34px; }
.controls button {
  background: var(--panel); color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; padding: 6px 12px; cursor: pointer;
}
.controls button:hover { border-color: var(--accent); }
.caption { color: var(--muted); font-family: var(--mono); font-size: 13px; }

#hexmap { width: 100%; max-width: 880px; height: auto; display: block; margin: 0 auto; }
.edge { stroke: var(--line); stroke-width: .5; transition: stroke .3s, opacity .3s; }
.edge.lit { stroke: var(--glow); filter: drop-shadow(0 0 1.5px var(--glow)); }
.node {
  fill: var(--panel); stroke: var(--accent); stroke-width: .6;
  transition: transform .2s, filter .2s;
}
.part[data-kind="port"] .node { stroke: var(--kind-port); }
.part[data-kind="driver"] .node { stroke: var(--kind-driver); }
.part[data-kind="core"] .node { stroke: var(--kind-core); }
.part[data-kind="ui"] .node { stroke: var(--kind-ui); }
.part[data-kind="store"] .node { stroke: var(--kind-store); }
.part { cursor: pointer; }
.part:hover .node, .part:focus .node { filter: drop-shadow(0 0 2px var(--glow)); }
.part.active .node { fill: var(--accent); }
.part:focus { outline: none; }
.label { fill: var(--muted); font-size: 3px; font-family: var(--font); pointer-events: none; }
#token { fill: var(--glow); filter: drop-shadow(0 0 3px var(--glow)); opacity: 0; }
#token.running { opacity: 1; }

.legend { display: flex; flex-wrap: wrap; gap: 14px; list-style: none; padding: 0; margin: 14px auto 0; max-width: 880px; }
.legend li { display: flex; align-items: center; gap: 6px; color: var(--muted); font-size: 13px; }
.legend .swatch { width: 12px; height: 12px; border-radius: 3px; }
.legend [data-kind="port"] .swatch { background: var(--kind-port); }
.legend [data-kind="driver"] .swatch { background: var(--kind-driver); }
.legend [data-kind="core"] .swatch { background: var(--kind-core); }
.legend [data-kind="ui"] .swatch { background: var(--kind-ui); }
.legend [data-kind="store"] .swatch { background: var(--kind-store); }

/* Drawer */
.drawer {
  position: fixed; top: 0; right: 0; height: 100vh; width: min(560px, 92vw);
  background: var(--panel); border-left: 1px solid var(--line);
  box-shadow: -20px 0 60px rgba(0,0,0,.4); padding: 24px 26px; overflow-y: auto;
  transform: translateX(100%); transition: transform .28s ease; z-index: 10;
}
.drawer.open { transform: translateX(0); }
.drawer .kind-badge {
  display: inline-block; font-size: 12px; text-transform: uppercase;
  letter-spacing: .05em; padding: 2px 8px; border-radius: 999px;
  border: 1px solid var(--line); color: var(--muted);
}
.drawer h2 { margin: 10px 0 4px; }
.drawer .tagline { color: var(--muted); margin-top: 0; }
.drawer .enforced { border-top: 1px solid var(--line); margin-top: 18px; padding-top: 14px; }
.drawer .enforced code, .drawer a { font-family: var(--mono); font-size: 13px; }
.drawer .adr { border-top: 1px solid var(--line); margin-top: 18px; padding-top: 14px; }
.drawer .close { position: absolute; top: 16px; right: 18px; background: transparent; border: 0; color: var(--muted); font-size: 22px; cursor: pointer; }

/* Doc pages + drawer markdown */
.doc, .drawer .adr { max-width: 820px; margin: 0 auto; }
.doc { padding: 32px clamp(14px, 5vw, 40px) 80px; }
.page-doc .doc h1 { margin-top: 0; }
.doc h1, .doc h2, .drawer .adr h1, .drawer .adr h2 { line-height: 1.25; }
.doc pre, .drawer .adr pre {
  background: var(--bg); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; overflow-x: auto;
}
.doc code, .drawer .adr code { font-family: var(--mono); font-size: .92em; }
.doc table, .drawer .adr table { border-collapse: collapse; width: 100%; overflow-x: auto; display: block; }
.doc th, .doc td, .drawer .adr th, .drawer .adr td { border: 1px solid var(--line); padding: 7px 10px; text-align: left; }
.doc blockquote, .drawer .adr blockquote { border-left: 3px solid var(--accent); margin: 0; padding: 2px 16px; color: var(--muted); }
a { color: var(--accent); }

.doc-index { max-width: 880px; margin: 8px auto 60px; padding: 0 clamp(14px, 5vw, 40px); }
.doc-index h2 { border-bottom: 1px solid var(--line); padding-bottom: 6px; margin-top: 28px; }
.doc-index ul { list-style: none; padding-left: 0; }
.doc-index li { padding: 3px 0; }

@media (prefers-reduced-motion: reduce) {
  #token, .drawer, .node, .edge { transition: none !important; }
  #token { display: none; }
}
```

- [ ] **Step 4: Run the guard test + rebuild visually**

Run: `uv run pytest tests/test_docs_site.py::test_app_css_defines_theme_and_kind_colours -q`
Expected: PASS.

Run: `python scripts/build_docs.py --out /tmp/site-check`
Then open `/tmp/site-check/index.html` in a browser (or the harness Board host) and confirm: dark theme renders, the hexmap shows all nodes with kind-coloured rings, the legend matches, and the theme toggle button is present. (Interactivity arrives in Task 5.)

- [ ] **Step 5: Commit**

```bash
git add src/harness_docs_site/assets/app.css tests/test_docs_site.py
git commit -m "feat(docs-site): dark/light theme, hexmap and drawer styling"
```

---

## Task 5: `app.js` — animation, drill-down drawer, hash router, theme toggle

**Files:**
- Rewrite: `src/harness_docs_site/assets/app.js`
- Test: `tests/test_docs_site.py` (one guard test asserting the JS wires the embedded data)

**Interfaces:**
- Consumes: `#model-data` and `#adr-html` JSON blocks, the `#hexmap` SVG (`.part[data-part-id]`, `.edge[data-src][data-dst]`, `#token`), and controls (`#play`, `#step`, `#caption`, `#drawer`, `#theme-toggle`) emitted by `site.py`.
- Produces: browser behavior only. Adds no server calls (no `fetch`, no external URLs).

**Context — behavior spec:**
- On load (explorer page only): parse both JSON blocks. Build a `parts` lookup by id.
- **Token animation:** walk `model.flow` stage-by-stage; for each consecutive pair, move `#token` from part A's `(x,y)` to B's `(x,y)` over ~900ms via `requestAnimationFrame` (linear interp), light the matching `.edge` (add `.lit`), mark the arriving `.part` `.active`, and set `#caption` to the stage caption. Auto-play once on load. `#play` toggles play/pause; `#step` advances one stage while paused.
- **Drill-down:** clicking a `.part` (or Enter on the focused group) pauses animation and opens the drawer for that id via the router (`location.hash = '#/part/' + id`).
- **Drawer render:** name, kind badge, tagline, description, an "Enforced by" block listing `sources` (as `<code>` — not links to nonexistent pages, just paths) and ADR links (`adr/<slug>.html`), then the full ADR HTML from `#adr-html[slug]` for each cited slug, then `related_docs` links if any. A close button and Esc set `location.hash = '#/'`.
- **Hash router:** `#/` closes the drawer; `#/part/<id>` opens that part's drawer (also on initial load, so deep links work). Ignore unknown ids.
- **Theme toggle:** `#theme-toggle` flips `document.documentElement.dataset.theme` between `dark`/`light` and persists to `localStorage['harness-docs-theme']`; on load, restore it. Present on every page (doc pages too), so guard for missing explorer elements.
- **Reduced motion:** if `matchMedia('(prefers-reduced-motion: reduce)').matches`, skip token motion — jump the caption/active state to the final stage immediately; `#step` still works.

- [ ] **Step 1: Guard test**

Append to `tests/test_docs_site.py`:

```python
def test_app_js_reads_embedded_data_and_has_no_external_calls(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    js = (tmp_path / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    assert "model-data" in js
    assert "adr-html" in js
    assert "fetch(" not in js  # fully client-side, no network
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_docs_site.py::test_app_js_reads_embedded_data_and_has_no_external_calls -q`
Expected: FAIL (placeholder JS).

- [ ] **Step 3: Write `app.js`**

Replace `src/harness_docs_site/assets/app.js` with:

```javascript
/* Architecture Explorer — fully client-side, no network calls. */
(function () {
  "use strict";

  // ---- Theme (every page) ----
  var THEME_KEY = "harness-docs-theme";
  function applyTheme(t) { document.documentElement.dataset.theme = t; }
  (function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) {}
    if (saved) applyTheme(saved);
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      applyTheme(next);
      try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
    });
  })();

  // ---- Explorer (index only) ----
  var svg = document.getElementById("hexmap");
  var modelEl = document.getElementById("model-data");
  if (!svg || !modelEl) return; // doc page: theme only

  var model = JSON.parse(modelEl.textContent);
  var adrHtml = JSON.parse(document.getElementById("adr-html").textContent || "{}");
  var partsById = {};
  model.parts.forEach(function (p) { partsById[p.id] = p; });

  var token = document.getElementById("token");
  var caption = document.getElementById("caption");
  var playBtn = document.getElementById("play");
  var stepBtn = document.getElementById("step");
  var drawer = document.getElementById("drawer");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function partGroup(id) { return svg.querySelector('.part[data-part-id="' + id + '"]'); }
  function edgeLine(a, b) {
    return svg.querySelector('.edge[data-src="' + a + '"][data-dst="' + b + '"]') ||
           svg.querySelector('.edge[data-src="' + b + '"][data-dst="' + a + '"]');
  }
  function clearHighlights() {
    svg.querySelectorAll(".part.active").forEach(function (g) { g.classList.remove("active"); });
    svg.querySelectorAll(".edge.lit").forEach(function (e) { e.classList.remove("lit"); });
  }
  function highlightStage(i) {
    var stage = model.flow[i];
    var p = partsById[stage.part_id];
    var g = partGroup(stage.part_id);
    if (g) g.classList.add("active");
    if (i > 0) {
      var e = edgeLine(model.flow[i - 1].part_id, stage.part_id);
      if (e) e.classList.add("lit");
    }
    if (caption) caption.textContent = stage.caption;
    if (token && p) { token.setAttribute("cx", p.x); token.setAttribute("cy", p.y); }
  }

  var stageIndex = 0;
  var rafId = null;
  var playing = false;

  function stopMotion() { if (rafId) cancelAnimationFrame(rafId); rafId = null; }
  function setPlayLabel() { if (playBtn) playBtn.textContent = playing ? "⏸ Pause" : "▶ Play"; }

  function animateTo(i, done) {
    if (reduce || i === 0) { highlightStage(i); done(); return; }
    var from = partsById[model.flow[i - 1].part_id];
    var to = partsById[model.flow[i].part_id];
    var start = null;
    var dur = 900;
    if (token) token.classList.add("running");
    var e = edgeLine(model.flow[i - 1].part_id, model.flow[i].part_id);
    if (e) e.classList.add("lit");
    function frame(ts) {
      if (start === null) start = ts;
      var t = Math.min(1, (ts - start) / dur);
      if (token) {
        token.setAttribute("cx", from.x + (to.x - from.x) * t);
        token.setAttribute("cy", from.y + (to.y - from.y) * t);
      }
      if (t < 1) { rafId = requestAnimationFrame(frame); }
      else { highlightStage(i); done(); }
    }
    rafId = requestAnimationFrame(frame);
  }

  function play() {
    playing = true; setPlayLabel();
    if (token) token.classList.add("running");
    function next() {
      if (!playing) return;
      animateTo(stageIndex, function () {
        stageIndex++;
        if (stageIndex >= model.flow.length) { playing = false; setPlayLabel(); return; }
        setTimeout(next, 260);
      });
    }
    next();
  }
  function pause() { playing = false; stopMotion(); setPlayLabel(); }

  if (playBtn) playBtn.addEventListener("click", function () {
    if (playing) { pause(); return; }
    if (stageIndex >= model.flow.length) { clearHighlights(); stageIndex = 0; }
    play();
  });
  if (stepBtn) stepBtn.addEventListener("click", function () {
    pause();
    if (stageIndex >= model.flow.length) { clearHighlights(); stageIndex = 0; }
    animateTo(stageIndex, function () { stageIndex++; });
  });

  // ---- Drill-down drawer + router ----
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function renderDrawer(part) {
    var h = [];
    h.push('<button class="close" aria-label="Close">×</button>');
    h.push('<span class="kind-badge">' + esc(part.kind) + "</span>");
    h.push("<h2>" + esc(part.name) + "</h2>");
    h.push('<p class="tagline">' + esc(part.tagline) + "</p>");
    h.push("<p>" + esc(part.description) + "</p>");
    h.push('<div class="enforced"><strong>Enforced by</strong><ul>');
    part.sources.forEach(function (s) { h.push("<li><code>" + esc(s) + "</code></li>"); });
    part.adrs.forEach(function (slug) {
      h.push('<li><a href="adr/' + esc(slug) + '.html">ADR ' + esc(slug) + "</a></li>");
    });
    (part.related_docs || []).forEach(function (slug) {
      h.push('<li><a href="spec/' + esc(slug) + '.html">' + esc(slug) + "</a></li>");
    });
    h.push("</ul></div>");
    part.adrs.forEach(function (slug) {
      if (adrHtml[slug]) h.push('<div class="adr">' + adrHtml[slug] + "</div>");
    });
    drawer.innerHTML = h.join("\n");
    drawer.hidden = false;
    requestAnimationFrame(function () { drawer.classList.add("open"); });
    drawer.querySelector(".close").addEventListener("click", function () { location.hash = "#/"; });
  }

  function openPart(id) {
    var part = partsById[id];
    if (!part) { location.hash = "#/"; return; }
    pause();
    clearHighlights();
    var g = partGroup(id);
    if (g) g.classList.add("active");
    renderDrawer(part);
  }
  function closeDrawer() {
    drawer.classList.remove("open");
    setTimeout(function () { drawer.hidden = true; drawer.innerHTML = ""; }, 280);
  }

  function route() {
    var m = /^#\/part\/(.+)$/.exec(location.hash);
    if (m) openPart(decodeURIComponent(m[1]));
    else closeDrawer();
  }
  window.addEventListener("hashchange", route);

  svg.querySelectorAll(".part").forEach(function (g) {
    var id = g.getAttribute("data-part-id");
    g.addEventListener("click", function () { location.hash = "#/part/" + id; });
    g.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); location.hash = "#/part/" + id; }
    });
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && !drawer.hidden) location.hash = "#/";
  });

  // Initial paint: deep-link honored, else auto-play once.
  if (/^#\/part\//.test(location.hash)) { route(); }
  else if (reduce) { highlightStage(model.flow.length - 1); stageIndex = model.flow.length; }
  else { play(); }
})();
```

- [ ] **Step 4: Run the guard test + full suite**

Run: `uv run pytest tests/test_docs_site.py::test_app_js_reads_embedded_data_and_has_no_external_calls -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: PASS (whole suite).

- [ ] **Step 5: Manual verification in a browser**

Run: `python scripts/build_docs.py --out /tmp/site-check`
Open `/tmp/site-check/index.html` and confirm:
- token auto-plays through the flow, lighting edges and captioning each stage;
- Play/Pause and Step work;
- clicking a part opens the drawer with its description, "Enforced by" paths, and the **full ADR text inline**;
- the drawer URL is deep-linkable (reload with `#/part/router` opens the router drawer);
- Esc / × closes; theme toggle flips dark/light and persists across reload;
- a doc page (e.g. `adr/0004-pure-router.html`) is styled and its back-link + theme toggle work.

- [ ] **Step 6: Commit**

```bash
git add src/harness_docs_site/assets/app.js tests/test_docs_site.py
git commit -m "feat(docs-site): token animation, drill-down drawer, hash router, theme toggle"
```

---

## Task 6: End-to-end verification, structure snapshot, and README pointer

**Files:**
- Test: `tests/test_docs_site.py` (structure snapshot)
- Modify: `README.md` (one line pointing at the live site — optional but recommended)

**Interfaces:**
- Consumes: everything above.
- Produces: a regression guard on the emitted structure; no new runtime surface.

- [ ] **Step 1: Structure snapshot test**

Append to `tests/test_docs_site.py`:

```python
def test_explorer_index_structure_snapshot(tmp_path: Path):
    _write_fixture_tree(tmp_path)
    build_site(discover_docs(tmp_path), tmp_path, tmp_path / "site")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    # Controls and containers the JS binds to must all be present.
    for needle in ('id="play"', 'id="step"', 'id="caption"', 'id="drawer"',
                   'id="token"', 'id="theme-toggle"', 'class="legend"'):
        assert needle in index, needle
    # Flow order is preserved in the embedded model.
    import json, re
    data = json.loads(re.search(
        r'id="model-data">(.*?)</script>', index, re.DOTALL).group(1))
    assert data["flow"][0]["part_id"] == "task-source"
    assert data["flow"][-1]["part_id"] == "github-source"
```

- [ ] **Step 2: Run to verify it passes** (the emitter already satisfies it)

Run: `uv run pytest tests/test_docs_site.py::test_explorer_index_structure_snapshot -q`
Expected: PASS. (If it fails, the `site.py` markup drifted from the JS contract — fix `site.py`, not the test.)

- [ ] **Step 3: Add a README pointer**

In `README.md`, under the `## Board` section (or near the top), add:

```markdown
## Documentation

The full architecture — an animated ports-and-adapters explorer you can drill
into, backed by the ADRs under `docs/adr/` — is published at
<https://onpaj.github.io/harness_v2/>. Rebuild it locally with
`python scripts/build_docs.py --out site` and open `site/index.html`.
```

- [ ] **Step 4: Full suite, lint-clean build, and Pages dry-run**

Run: `uv run pytest -q`
Expected: PASS (entire suite, no skips beyond the pre-existing one).

Run: `python scripts/build_docs.py --out site && grep -REl "https?://" site || echo "NO EXTERNAL URLS"`
Expected: the only matches are the intentional `https://onpaj.github.io/...` link inside rendered README/doc pages (external *links* in prose are fine); confirm no external `<script>`/`<link>`/`src`/`href` to a CDN in `assets/` or the shell. (The no-external-URL test already guards `.css`/`.js` and the index shell.)

- [ ] **Step 5: Commit**

```bash
git add tests/test_docs_site.py README.md
git commit -m "test(docs-site): explorer structure snapshot; docs pointer in README"
```

---

## Self-Review

**Spec coverage:**
- Full rebuild, everything interactive → Tasks 3–5 (explorer index, restyled doc pages, shared shell). ✓
- Python generator → self-contained static, no deps → Global Constraints; `shutil.copytree` of `assets/`, embedded JSON, no `fetch`. ✓
- Signature animation (token through hexagon, click to pause + drill) → Task 5 animation + drawer. ✓
- Architecture model as source of truth + validation test → Tasks 1–2. ✓
- Drill-down = curated summary + **full inline ADR** → `renderDrawer` + `#adr-html` map (Tasks 3, 5). ✓
- Deep docs still rendered per document → `_doc_page` (Task 3). ✓
- Hash routing / linkable parts → Task 5 router. ✓
- Dark default + light variant, reduced-motion, keyboard → Task 4 CSS + Task 5 JS. ✓
- Testing: model validation, build smoke (links/assets/no-external-URL/ADR-in-data), snapshot → Tasks 1–3, 6. ✓
- Deployment unchanged (Pages workflow) → Global Constraints; `build_docs.py` untouched. ✓

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to". The empty asset files in Task 3 are explicit, temporary, and replaced with complete files in Tasks 4–5. The deliberately-broken CSS line carries its own removal instruction. ✓

**Type consistency:** `Part`/`Stage`/`Edge`/`ArchitectureModel` field names are identical across `architecture.py`, `model_to_dict` (via `asdict`), the embedded JSON, and the JS (`p.id`, `p.x`, `p.y`, `p.kind`, `p.sources`, `p.adrs`, `p.related_docs`, `stage.part_id`, `stage.caption`, `edge.src`/`edge.dst`). `build_site(entries, repo_root, out_dir)` signature unchanged from the original. ✓
