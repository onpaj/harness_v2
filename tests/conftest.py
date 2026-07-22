"""Session-wide test setup.

`harness_docs_site` is deliberately excluded from the installed package (see
`pyproject.toml`'s `tool.setuptools.packages.find.exclude`), so unlike
`harness` it isn't reachable through the editable install. This inserts `src/`
onto `sys.path` so `tests/test_docs_site.py` can import it directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
