# Architecture Explorer — an interactive, animated documentation site

Status: draft
Date: 2026-07-22

## Goal

The current documentation site (`src/harness_docs_site/`, driven by
`scripts/build_docs.py`) is a plain drill-down: it discovers Markdown in four
fixed locations and renders one HTML page per document. It is correct and
durable, but it does not *show* how the harness works — a reader still has to
assemble the ports-and-adapters picture in their head from thirteen separate
ADRs.

This spec replaces that generator with an **Architecture Explorer**: a
self-contained static site whose home page is an interactive, animated map of
the harness. A hexagonal ports-and-adapters diagram is the centrepiece; a
glowing "task" token travels the real pipeline — GitHub issue → queue → router →
agent (in a worktree) → landing → PR — lighting each part as it passes. Clicking
a part pauses the animation and opens a detail drawer with everything about that
part, drilling all the way down into the full ADR that grounds it.

Every architectural surface gets the new treatment: the explorer, the per-part
drawers, and the rendered documents all share one animated, dark-themed shell
(with a light-mode variant). It stays a **dependency-free static site** built by
the existing Python toolchain and deployed by the existing GitHub Pages workflow
(`.github/workflows/pages.yml`) — no framework, no npm build, no runtime CDN.

## Non-goals (YAGNI)

- **No search.** The corpus is ~25 documents; a filter is not worth the weight.
- **No JS framework and no build toolchain.** Output is hand-authored HTML/CSS
  and vanilla JS emitted by Python, exactly as today.
- **No auto-derivation of the diagram from source code.** The architecture model
  is hand-authored (see below). Parsing the live module graph into a clean,
  legible hexagon is unreliable and out of scope.
- **No runtime server.** The site is static files; all interactivity is
  client-side over data embedded at build time.

## The architecture model — the single source of truth

A new module, `src/harness_docs_site/architecture.py`, holds a curated,
hand-authored description of the system as a small graph. It is plain Python
dataclasses (frozen), in the same spirit as `corpus.py`'s `DocEntry`.

### Parts (nodes)

Roughly twelve parts, each a frozen dataclass:

```
Part(
  id:          str            # stable slug, e.g. "task-source"; used in URLs
  name:        str            # display name, e.g. "TaskSource"
  kind:        Literal["port", "driver", "core", "ui", "store"]
  tagline:     str            # one line shown on hover / in the diagram
  description: str            # a paragraph of curated prose for the drawer
  adrs:        list[str]      # ADR slugs that ground this part, e.g. ["0001-ports-and-adapters"]
  sources:     list[str]      # repo-relative source paths, e.g. ["src/harness/ports/task_source.py"]
  invariants:  list[int]      # CLAUDE.md invariant numbers this part upholds
)
```

The initial set (final names/kinds to be confirmed against the tree during
implementation):

| id | kind | grounds in ADR |
|----|------|----------------|
| `task-source` | port | 0010 |
| `github-adapter` | driver | 0010, 0008 |
| `repo-registry` | store | 0008 |
| `queues` | core | 0003 |
| `router` | core | 0004 |
| `agent-runner` | core | 0002, 0007 |
| `persona-catalog` | store | 0007 |
| `worktree` | driver | 0006, 0009 |
| `artifact-folder` | store | 0006 |
| `landing` | core | 0009 |
| `board` | ui | 0005, 0011, 0012 |
| `board-view` / `task-control` / `stage-output-view` | port | 0005, 0011, 0012 |

### Flow (the animated journey)

An ordered list of stages the token travels through. Each stage references a
part `id` and carries a short caption describing what happens there:

```
Stage(part_id: str, caption: str)   # e.g. ("queues", "claimed atomically by rename")
```

The canonical flow: `task-source → queues → router → agent-runner → worktree →
landing → github-adapter` (issue in, PR out), with captions drawn from the ADRs
("landing proposes a PR, never touches main", etc.).

### Edges

The connections drawn between parts in the diagram (a list of
`(from_id, to_id)` pairs). Ports sit on the hexagon edge, the pure core inside,
drivers outside — the conventional hexagonal layout. Node positions are authored
in the model (or in the SVG template) rather than auto-laid-out.

### Validation (a test enforces model/doc coherence)

`architecture.py` exposes a `validate(model, repo_root)` that fails if:

- any `Part.adrs` slug has no matching `docs/adr/<slug>.md`;
- any `Stage.part_id` or edge endpoint is not a defined `Part.id`;
- any `Part.sources` path does not exist in the tree;
- any `Part` is orphaned (not on any edge and not in the flow).

This is what keeps the diagram from silently drifting away from the real
architecture as ADRs and code evolve. It runs in the test suite (below), so a
rename that breaks the mapping fails CI.

## What the reader sees and does

### Explorer (home, `index.html`)

The animated hexagon. On load, the token auto-plays the journey once, then
rests. Controls: **play / pause**, **step** (advance one stage), **speed**, and
a **legend** mapping colour to `kind` (port / driver / core / ui / store).
Data-flow lines glow as the token traverses them.

### Drill-down (per-part drawer)

Clicking a part (or focusing it and pressing Enter) pauses the animation,
highlights the part, and slides in a drawer containing, in order:

1. name, kind badge, tagline;
2. the curated `description` paragraph;
3. an **"Enforced by"** block — the invariants and the source files, as links;
4. the **full rendered ADR(s)** inline (reusing `markdown.py`), so "click ports →
   see all the info about how ports are designed" is literally satisfied;
5. links to related specs/plans.

### Deep docs

Every ADR, spec, plan, `README.md`, and `CLAUDE.md` is still rendered as its own
styled page in the shared dark theme, reachable from the parts that cite it and
from a docs index. This reuses the existing corpus discovery and Markdown
rendering.

### Linkability

Client-side **hash routing**: `#/`, `#/part/<id>`, `#/doc/<category>/<name>`.
Every part and document has a shareable URL, and the site remains pure static
files (no server rewrites needed).

## Implementation

Extends the existing package; no new dependencies.

- **`architecture.py`** (new) — the model dataclasses, the concrete model
  instance, and `validate()`.
- **`corpus.py`** (reused) — document discovery, unchanged in shape; the ADR
  slugs it produces are what parts reference.
- **`markdown.py`** (reused) — Markdown → HTML, used both for doc pages and for
  the inline ADR inside a part drawer. Restyled via CSS, not re-implemented.
- **`site.py`** (rewritten) — emits the new template set, embeds the
  architecture model and the doc index as JSON `<script type="application/json">`
  blocks, and copies the inlined assets into `site/assets/`.
- **Assets** (new, under the package, copied verbatim into output):
  - `app.css` — dark theme with a light-mode variant (via
    `prefers-color-scheme` plus an explicit toggle), the hexagon styling, drawer,
    doc typography.
  - `app.js` — hash router, drawer renderer (reads the embedded JSON), animation
    controller.
  - the hexagon itself is **SVG**; the token animates along SVG paths (either
    `<animateMotion>` or JS via `path.getPointAtLength`), so motion needs no
    library.
- **`scripts/build_docs.py`** — unchanged entry point and `--out` flag; already
  wired into `.github/workflows/pages.yml`.

### Motion, accessibility, performance

- **`prefers-reduced-motion`**: the token animation degrades to a static
  highlighted path plus the step control — no continuous motion.
- **Keyboard**: parts are focusable in flow order; Enter opens the drawer, Esc
  closes it; the doc index and nav are tab-navigable.
- **Self-contained**: no external fonts, scripts, styles, or images — everything
  is emitted into `site/`. A build-time check (and a test) asserts zero external
  URLs in the output.

## Testing

Extends the existing suites (`tests/test_architecture.py`,
`tests/test_docs_site.py`), all in-memory, no new deps:

- **Model validation** — `validate()` passes on the shipped model; targeted
  failing cases (unknown ADR slug, orphan part, dangling stage, missing source
  path) each raise.
- **Build smoke** — building into a temp dir emits `index.html`, the embedded
  model JSON, a page per discovered document, and the assets; every internal
  link resolves; the output contains no external URLs; the ADR referenced by a
  part appears in that part's data.
- **Structure snapshot** — a light assertion on key generated structure (the
  parts present in the embedded JSON, the flow order) so regressions in the
  emitter are caught.

## Deployment

Unchanged from the Pages work already merged: `build_docs.py` runs in
`.github/workflows/pages.yml` on push to `main` and publishes `site/` to
`https://onpaj.github.io/harness_v2/`. This spec adds no deployment surface.
