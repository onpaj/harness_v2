#!/usr/bin/env python3
"""Build the static HTML drill-down over this repo's own documentation.

Usage: python scripts/build_docs.py [--out DIR]   (default DIR: site)

Not wired into the installed `harness` CLI — this is a maintainer/CI concern
over this repository's own source tree, not something `harness run` needs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from harness_docs_site.corpus import discover_docs  # noqa: E402
from harness_docs_site.site import build_site  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site", help="output directory (default: site)")
    args = parser.parse_args(argv)

    out_dir = REPO_ROOT / args.out
    entries = discover_docs(REPO_ROOT)
    build_site(entries, REPO_ROOT, out_dir)
    print(f"wrote {len(entries)} document(s) to {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
