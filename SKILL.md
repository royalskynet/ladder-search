---
name: ladder-search
description: Web search and page reading with an escalation ladder. Use whenever the task needs information from the web - searching for sources, reading a URL, checking a platform (X, Reddit, YouTube, GitHub, Xiaohongshu, Bilibili), or a page that WebFetch could not read (blocked, login wall, JavaScript-rendered). Finds sources in parallel, then reads pages via Jina -> camofox -> logged-in Chrome (CDP), moving up one rung only when blocked.
metadata:
  version: 0.1.0
---

# ladder-search

Four layers. Use them together, not WebFetch alone. Never retry the same method on the same target: move one rung up.

| Need | Use |
|---|---|
| ① Find sources | Host `WebSearch` **and** `ladder.py search "<q>"` (Exa) in the same turn |
| ② Read a known URL | `ladder.py read <url>...` (Jina → camofox → CDP, auto-escalating). `WebFetch` only to pull one specific field. Raw HTML / JSON-LD: `curl -s` |
| ③ Named platform (X, Reddit, YouTube transcripts, Bilibili, Xiaohongshu, GitHub, Xueqiu) | `agent-reach` skill, its backend for that platform |
| ④ Login state, clicking, forms, infinite scroll, or ①–③ failed twice on the same target | `web-access` skill (CDP into your Chrome). Chrome not running: `scripts/wa-chrome start`; a site you logged into after the last sync: `scripts/wa-chrome sync` |

## Commands

```bash
S="${CLAUDE_SKILL_DIR}/scripts/ladder.py"
python3 "$S" doctor                         # which rungs are usable now
python3 "$S" search "query" --max 5         # TITLE<TAB>URL per hit; Exa via mcporter, ddgs fallback
python3 "$S" read URL1 URL2 @urls.txt       # URL<TAB>STATUS<TAB>RUNG<TAB>TITLE<TAB>BODY
python3 "$S" read URL --from cdp            # known login wall: start at the logged-in Chrome rung
```

Exit codes: `0` ok · `2` some URL failed or no hits · `3` no backend reachable (run `doctor`, then fall back to host tools) · `5` still blocked at the top rung (report it as a gap; do not loop).

## Read ladder

1. **jina**: `r.jina.ai/<url>`. Static, cheap, returns Markdown. Up to 4 URLs in parallel.
2. **camofox**: anti-detect Firefox REST service (`CAMOFOX_URL`, default `localhost:9377`). Handles bot walls. Has no login.
3. **cdp**: background tab in the Chrome that `web-access` controls (`CDP_PROXY_URL`, default `localhost:3456`). Uses your login state. Waits up to 10 s for dynamic content, then closes the tab.

A URL moves up only when the rung returns `BLOCKED` (401/403/429/503, body under 500 chars, or a captcha / login / cookie-wall marker), `EMPTY`, or `ERR`. Unreachable rungs are skipped. Browser rungs run serially with ≥3 s between hits on one domain.

## Rules

- A page blocked at every rung is a finding: say "could not read X (BLOCKED:marker)" and cite another source. Do not claim the content.
- `search` with zero hits does not mean no data exists. Rephrase once, or use the other search lane.
- The CDP rung acts as you. Use it to read pages. Posting, buying, or changing account settings goes through `web-access` with explicit user approval.

## Setup (all optional; missing parts degrade, they don't break)

`scripts/setup.sh` checks everything below and prints the exact commands. Run it first and show the user the plan. `scripts/setup.sh --apply` installs. It works at user level only (`npm -g`, `uv tool`/`pipx`, Claude Code plugin/skill dirs) and never uses sudo.

| Part | `--apply` runs |
|---|---|
| Exa search | `npm i -g mcporter`; `mcporter config add exa --url https://mcp.exa.ai/mcp` (free tier, no key) |
| Agent-Reach | `uv tool install` (or `pipx install`) `https://github.com/Panniantong/agent-reach/archive/main.zip`; `agent-reach skill --install` |
| web-access | `claude plugin marketplace add https://github.com/eze-is/web-access`; `claude plugin install web-access@web-access --scope user` (no `claude` CLI: `git clone` into `~/.claude/skills/web-access`). Needs Node.js 22+ |

Not run automatically, need the user's explicit OK: `agent-reach install --env=auto --system [--channels=...]` (system dependencies, global tools, platform logins), then `agent-reach doctor`. Restart Claude Code afterwards so new skills load, then run `ladder.py doctor`.

Optional rungs, set up by hand:
- ddgs fallback: `uv` installed (`uvx --from ddgs` runs on demand).
- camofox: [camofox-browser](https://github.com/jo-inc/camofox-browser). Install it in its own directory, not inside another app's `node_modules`. Run `npx camoufox-js fetch`, then run `server.js` as a service. Set `CAMOFOX_URL` if it is not on `:9377`.
- CDP on macOS: `scripts/wa-chrome start` runs a separate Chrome on a copy of your profile, so your everyday Chrome keeps working. Point web-access at `~/.web-access/chrome-data` on its first run.
