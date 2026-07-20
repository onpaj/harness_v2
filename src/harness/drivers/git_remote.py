"""Derive a GitHub `owner/repo` slug from a clone's git `origin` remote.

`repos.json` maps a repo name to a local path; it holds no GitHub slug. Rather
than duplicate that fact in config, we read it from the clone itself. A repo
whose origin is not a GitHub URL yields None and is simply not scanned.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def parse_github_slug(remote_url: str) -> str | None:
    """Map a git remote URL to `"owner/repo"`, or None if it is not github.com.

    Handles the SSH (`git@github.com:owner/repo.git`) and HTTPS
    (`https://github.com/owner/repo.git`) forms, with or without a `.git`
    suffix.
    """
    url = remote_url.strip()
    if not url:
        return None

    if url.startswith("git@"):
        _, _, rest = url.partition("@")  # github.com:owner/repo.git
        host, _, path = rest.partition(":")
    elif "://" in url:
        _, _, rest = url.partition("://")  # [creds@]github.com/owner/repo.git
        rest = rest.rsplit("@", 1)[-1]  # drop optional credentials
        host, _, path = rest.partition("/")
    else:
        return None

    if host != "github.com":
        return None

    path = path.removesuffix(".git").strip("/")
    parts = path.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0]}/{parts[1]}"


def github_slug(path: Path) -> str | None:
    """The GitHub slug of the clone at `path`, or None if it has no GitHub
    origin (not a git repo, no `origin`, non-GitHub remote, or git missing)."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return parse_github_slug(result.stdout)
