"""Standalone static-site generator for this repo's own documentation.

Not part of the `harness` package: it reads Markdown files under `docs/` (plus
`README.md`/`CLAUDE.md`) and writes plain HTML, with no dependency on
`harness` itself and no runtime relationship to the orchestration loop. See
`scripts/build_docs.py` for the entry point.
"""
