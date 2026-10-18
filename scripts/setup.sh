#!/usr/bin/env bash
# setup.sh: install the tools ladder-search routes to. Check-only by default; --apply installs.
#   setup.sh            list what is missing and the exact commands that would run
#   setup.sh --apply    run them (user-level only: npm -g, uv/pipx tool, Claude Code plugin/skill dirs)
# Never runs sudo, never runs `agent-reach install --system` (system deps + platform logins need the user's
# explicit OK, per Agent-Reach's own install guide); those are printed as next steps instead.
set -uo pipefail
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AR_SRC="https://github.com/Panniantong/agent-reach/archive/main.zip"
WA_REPO="https://github.com/eze-is/web-access"
SKILLS="$HOME/.claude/skills"
TODO=0

have() { command -v "$1" >/dev/null 2>&1; }
step() {  # step "<label>" <command...>: run with --apply, otherwise print
  TODO=$((TODO + 1))
  if [ "$APPLY" = 1 ]; then
    echo "+ $*"
    "$@" || { echo "FAIL: $1 ($*)" >&2; return 1; }
  else
    echo "  would run: $*"
  fi
}
ok() { echo "ok   $1"; }
miss() { echo "miss $1"; }

echo "[1/4] Exa search (mcporter)"
if have mcporter; then ok "mcporter"; else
  miss "mcporter"
  if have npm; then step npm i -g mcporter; else echo "  need Node.js/npm first: https://nodejs.org"; fi
fi
# capture first: with pipefail, `grep -q` exiting early SIGPIPEs the producer and fails the pipeline
if have mcporter && grep -qw exa <<<"$(mcporter config list 2>/dev/null)"; then ok "exa server"; else
  miss "exa server in mcporter"
  step mcporter config add exa --url https://mcp.exa.ai/mcp --scope home  # default scope writes ./config
fi

echo "[2/4] Agent-Reach (platform backends: X, Reddit, YouTube, Bilibili, Xiaohongshu, ...)"
if have agent-reach; then ok "agent-reach $(agent-reach --version 2>/dev/null | head -1)"; else
  miss "agent-reach"
  if have uv; then step uv tool install "$AR_SRC"
  elif have pipx; then step pipx install "$AR_SRC"
  else echo "  need uv (https://docs.astral.sh/uv/) or pipx first"; fi
fi
if [ -f "$SKILLS/agent-reach/SKILL.md" ]; then ok "agent-reach skill"; else
  miss "agent-reach skill"
  # uv/pipx put the binary in ~/.local/bin, which may not be on PATH in this shell yet
  step "$(command -v agent-reach || echo "$HOME/.local/bin/agent-reach")" skill --install
fi

echo "[3/4] web-access (CDP into your Chrome; needs Node.js 22+)"
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
[ "$NODE_MAJOR" -ge 22 ] && ok "node $NODE_MAJOR" || miss "node >= 22 (found ${NODE_MAJOR}); install from https://nodejs.org"
if [ -f "$SKILLS/web-access/SKILL.md" ] || (have claude && grep -q 'web-access' <<<"$(claude plugin list 2>/dev/null)"); then
  ok "web-access"
else
  miss "web-access"
  if have claude; then
    step claude plugin marketplace add "$WA_REPO"
    step claude plugin install web-access@web-access --scope user
  else
    step git clone "$WA_REPO" "$SKILLS/web-access"
  fi
fi

echo "[4/4] optional rungs (not installed by this script)"
have uvx && ok "uvx (ddgs search fallback)" || echo "info install uv for the ddgs search fallback"
echo "info camofox rung: npm i @askjo/camofox-browser in its own directory (not inside another app),"
echo "     then 'npx camoufox-js fetch' and run its server.js as a service; set CAMOFOX_URL if not :9377"
[ "$(uname)" = Darwin ] && echo "info CDP rung on macOS: $HERE/wa-chrome start   (copies your Chrome login state to a separate profile)"

echo
if [ "$APPLY" = 1 ]; then
  echo "Next, with the user's explicit OK only (system deps, global tools, platform logins):"
  echo "  agent-reach install --env=auto --system [--channels=twitter,reddit,bilibili,xiaohongshu,...]"
  echo "  agent-reach doctor"
  echo "Restart Claude Code so new skills/plugins load, then:"
  echo "  python3 $HERE/ladder.py doctor"
elif [ "$TODO" -gt 0 ]; then
  echo "$TODO step(s) pending. Re-run with --apply to install."
else
  echo "Everything this script manages is installed."
fi
