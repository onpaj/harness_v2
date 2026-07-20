#!/usr/bin/env bash
#
# harness installer — bootstrap a working harness from a fresh clone.
#
# One command takes a newcomer from `git clone` to a runnable harness:
#   1. checks prerequisites (Python 3.11+, git; warns if the `claude` CLI is
#      missing, which real runs need),
#   2. creates a virtualenv and installs the package editable with its dev
#      extras,
#   3. runs `harness init` to write the workflow, the default agent personas
#      and an empty repos.json,
#   4. walks you through populating repos.json (repo name -> local path).
#
# It is safe to re-run: an existing venv is reused, `harness init` never
# overwrites existing files, and the repos.json step only adds entries.
#
# Usage:
#   ./install.sh [--root DIR] [--workflow NAME] [--yes] [--help]
#
#   --root DIR        harness home to initialize (default: $HARNESS_HOME or
#                     ~/.harness). Mirrors `harness --root`.
#   --workflow NAME   workflow to initialize (default: default).
#   --yes, -y         non-interactive: skip the repos.json wizard and just
#                     print how to populate it by hand.
#   --help, -h        show this help and exit.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration and argument parsing
# ---------------------------------------------------------------------------

# The repo root is the directory this script lives in, so the installer works
# regardless of the caller's current directory.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

MIN_PY_MINOR=11  # requires-python = ">=3.11" in pyproject.toml

ROOT=""
WORKFLOW="default"
INTERACTIVE=1

usage() {
    sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//; s/^#$//' | sed '$d'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --root)
            ROOT="${2:?--root needs a value}"
            shift 2
            ;;
        --root=*)
            ROOT="${1#--root=}"
            shift
            ;;
        --workflow)
            WORKFLOW="${2:?--workflow needs a value}"
            shift 2
            ;;
        --workflow=*)
            WORKFLOW="${1#--workflow=}"
            shift
            ;;
        -y|--yes)
            INTERACTIVE=0
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            echo "run './install.sh --help' for usage" >&2
            exit 2
            ;;
    esac
done

# Resolve the harness home the same way the CLI does, so the wizard reads the
# very repos.json that `harness init` just wrote.
if [ -z "${ROOT}" ]; then
    ROOT="${HARNESS_HOME:-${HOME}/.harness}"
fi
# Expand a leading ~ (the CLI uses Path.expanduser()).
case "${ROOT}" in
    "~") ROOT="${HOME}" ;;
    "~/"*) ROOT="${HOME}/${ROOT#\~/}" ;;
esac

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------

say "Checking prerequisites"

command -v git >/dev/null 2>&1 || die "git is not installed or not on PATH."

# Find a Python interpreter that is >= 3.11. Prefer an explicit python3.11, then
# fall back to python3 if it is new enough.
pyver_ok() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, '"${MIN_PY_MINOR}"') else 1)' \
        >/dev/null 2>&1
}

PYTHON=""
for candidate in python3.11 python3.12 python3.13 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && pyver_ok "$candidate"; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "${PYTHON}" ] || die "Python 3.${MIN_PY_MINOR}+ is required but was not found on PATH."
say "Using $("$PYTHON" --version 2>&1) at $(command -v "$PYTHON")"

# The `claude` CLI is only needed for real runs (the agent behind `claude -p`);
# init, submit and the in-memory tests do not need it. Warn, don't fail.
if ! command -v claude >/dev/null 2>&1; then
    warn "the 'claude' CLI is not on PATH — real 'harness run' needs it, but installation continues."
fi

# ---------------------------------------------------------------------------
# 2. Virtualenv + editable install
# ---------------------------------------------------------------------------

if [ -d "${VENV_DIR}" ]; then
    say "Reusing existing virtualenv at ${VENV_DIR}"
else
    say "Creating virtualenv at ${VENV_DIR}"
    "$PYTHON" -m venv "${VENV_DIR}"
fi

VENV_PY="${VENV_DIR}/bin/python"
[ -x "${VENV_PY}" ] || die "virtualenv looks broken: ${VENV_PY} is missing."

say "Installing harness (editable, with dev extras)"
"${VENV_PY}" -m pip install --quiet --upgrade pip
"${VENV_PY}" -m pip install --quiet -e "${REPO_ROOT}[dev]"

HARNESS="${VENV_DIR}/bin/harness"
[ -x "${HARNESS}" ] || die "the 'harness' entry point was not installed at ${HARNESS}."

# ---------------------------------------------------------------------------
# 3. harness init
# ---------------------------------------------------------------------------

say "Initializing harness home at ${ROOT}"
"${HARNESS}" init --root "${ROOT}" --workflow "${WORKFLOW}"

REPOS_JSON="${ROOT}/repos.json"

# ---------------------------------------------------------------------------
# 4. Populate repos.json (repo name -> local path)
# ---------------------------------------------------------------------------
#
# `harness init` leaves repos.json as an empty object. The task's `repository`
# is a name; the registry resolves it to a path from here. Without at least one
# entry a real run cannot attach a worktree.

# Merge a single name->path entry into repos.json without clobbering the rest.
# Uses the freshly installed venv Python so we never hand-roll JSON in bash.
add_repo_entry() {
    "${VENV_PY}" - "${REPOS_JSON}" "$1" "$2" <<'PY'
import json
import pathlib
import sys

path_to_repos, name, repo_path = sys.argv[1], sys.argv[2], sys.argv[3]
target = pathlib.Path(path_to_repos)
data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
data[name] = repo_path
target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
}

count_repo_entries() {
    "${VENV_PY}" - "${REPOS_JSON}" <<'PY'
import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
print(len(data))
PY
}

existing_count="$(count_repo_entries)"

manual_repos_hint() {
    cat <<EOF
Edit ${REPOS_JSON} to map each repo name to its absolute path on this machine, e.g.:

  {
    "app-backend": "/home/you/code/app-backend",
    "app-frontend": "/home/you/code/app-frontend"
  }

The name is what you pass to 'harness submit --repo <name>'; the harness derives
the per-task worktree path itself.
EOF
}

if [ "${INTERACTIVE}" -eq 0 ]; then
    say "Skipping the repos.json wizard (--yes)"
    if [ "${existing_count}" -eq 0 ]; then
        manual_repos_hint
    fi
elif [ ! -t 0 ]; then
    # No TTY (piped / CI): don't block on a prompt that can't be answered.
    warn "no interactive terminal — skipping the repos.json wizard."
    if [ "${existing_count}" -eq 0 ]; then
        manual_repos_hint
    fi
else
    if [ "${existing_count}" -gt 0 ]; then
        say "repos.json already has ${existing_count} entr$([ "${existing_count}" -eq 1 ] && echo y || echo ies) — you can add more (leave the name blank to finish)."
    else
        say "Let's add the repositories the harness will work on (leave the name blank to finish)."
    fi
    added=0
    while true; do
        printf 'repo name (blank to finish): '
        IFS= read -r name || break
        [ -n "${name}" ] || break
        printf 'absolute path for "%s": ' "${name}"
        IFS= read -r repo_path || break
        if [ -z "${repo_path}" ]; then
            warn "empty path — skipping ${name}."
            continue
        fi
        case "${repo_path}" in
            "~") repo_path="${HOME}" ;;
            "~/"*) repo_path="${HOME}/${repo_path#\~/}" ;;
        esac
        if [ ! -d "${repo_path}" ]; then
            warn "'${repo_path}' is not an existing directory — recorded anyway; fix it in ${REPOS_JSON} if it's wrong."
        fi
        add_repo_entry "${name}" "${repo_path}"
        added=$((added + 1))
        say "recorded ${name} -> ${repo_path}"
    done
    if [ "${added}" -eq 0 ] && [ "${existing_count}" -eq 0 ]; then
        warn "no repositories recorded."
        manual_repos_hint
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

say "Harness installed."
cat <<EOF

Next steps:
  - Activate the venv:   source ${VENV_DIR}/bin/activate
    (or call tools directly, e.g. ${HARNESS} ...)
  - Submit a task:       harness submit --root ${ROOT} --repo <name> --data '{"request": "..."}'
  - Start the loop:      harness run --root ${ROOT}
    The board is served at http://127.0.0.1:8420/ (use --api-port 0 to disable).

Repos are configured in ${REPOS_JSON}.
EOF
