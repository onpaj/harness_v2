#!/bin/sh
# Install agentharness as a launchd user agent.
#
#   ./deploy/install.sh --print     render the plist to stdout, change nothing
#   ./deploy/install.sh             render, install, and start the service
set -eu

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
TEMPLATE="$REPO_ROOT/deploy/com.agentharness.plist.template"
LABEL="com.agentharness"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
HARNESS_HOME="${AGENTHARNESS_HOME:-$HOME/.agentharness}"

render() {
    sed \
        -e "s|__PYTHON__|$PYTHON|g" \
        -e "s|__HARNESS_HOME__|$HARNESS_HOME|g" \
        -e "s|__HOME__|$HOME|g" \
        -e "s|__PATH__|$PATH|g" \
        "$TEMPLATE"
}

if [ "${1:-}" = "--print" ]; then
    render
    exit 0
fi

if [ ! -x "$PYTHON" ]; then
    echo "error: no interpreter at $PYTHON" >&2
    echo "create it with: python3.11 -m venv .venv && .venv/bin/pip install -e ." >&2
    exit 1
fi

if [ ! -d "$HARNESS_HOME" ]; then
    echo "error: $HARNESS_HOME does not exist" >&2
    echo "run 'agentharness init' before installing the service" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HARNESS_HOME/logs"
render > "$PLIST"

# Replace any previous registration, then start.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "installed $PLIST"
echo "restart with: launchctl kickstart -k gui/\$(id -u)/$LABEL"
echo "logs:         $HARNESS_HOME/logs/serve.err.log"
