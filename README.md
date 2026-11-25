# ladder-search

A self-contained Claude Code + Codex plugin that routes every web task to the right tool: search lanes, a page-reading ladder, [Agent-Reach](https://github.com/Panniantong/Agent-Reach) for named platforms, and [web-access](https://github.com/eze-is/web-access) for logged-in or interactive browsing. It contains no code from either project. It calls them as dependencies and decides which one handles each request.

Agents tend to try one host fetch call and give up when the page is blocked. ladder-search runs a fixed procedure instead:
search in parallel, read each page on the cheapest rung that works, and move up one rung only when the page is blocked.

```
search   host search tool  +  Exa (mcporter)  ->  TinyFish  ->  ddgs fallback
read     tinyfish (TinyFish Fetch, free)  ->  jina (r.jina.ai)  ->  camofox (anti-detect Firefox)  ->  cdp (your logged-in Chrome via web-access)
platform  X / Reddit / YouTube / Bilibili / Xiaohongshu / Weibo / Xueqiu / GitHub  ->  agent-reach
interact  login, clicks, forms, or 1-3 failed twice  ->  web-access (CDP)
```

Each rung is tried at most once per URL. Unreachable rungs are skipped. A page blocked at the top rung is reported as a gap (exit 5), never retried in a loop.

## Install

### Claude Code

As a plugin (skill + a `UserPromptSubmit` hook that injects the rule on web-lookup prompts):

```
/plugin marketplace add royalskynet/ladder-search
/plugin install ladder-search@ladder-search
```

### Codex

The repository is also a portable Agent Plugins package: root `plugin.json` plus the canonical skill in `skills/ladder-search/`.

```bash
codex plugin marketplace add royalskynet/ladder-search
codex plugin add ladder-search@ladder-search
```

Codex reads the existing `.claude-plugin/marketplace.json` as a legacy-compatible marketplace. The bundled `UserPromptSubmit` hook uses the same output contract on Codex, but like every non-managed Codex hook it must be reviewed and trusted in `/hooks` before it runs.

### Skill only

No hook; the model decides from the description when to use it. The repository root keeps `SKILL.md` and `scripts` as compatibility symlinks into `skills/ladder-search/` on POSIX checkouts, while plugin installs do not depend on those symlinks.

```bash
git clone https://github.com/royalskynet/ladder-search ~/.agents/skills/ladder-search   # open agent skills / Codex
git clone https://github.com/royalskynet/ladder-search ~/.claude/skills/ladder-search  # Claude-only setup
```

Then check what is available from a source checkout or direct skill clone:

```bash
python3 skills/ladder-search/scripts/ladder.py doctor   # source checkout
python3 ~/.agents/skills/ladder-search/scripts/ladder.py doctor  # direct clone
```

Only Jina is always usable. It needs no key or install. To install the rest (TinyFish Fetch, Exa search, Agent-Reach, web-access):

```bash
skills/ladder-search/scripts/setup.sh            # check only: lists what is missing and the commands it would run
skills/ladder-search/scripts/setup.sh --apply    # install, user-level only, no sudo
```

Agent-Reach's system-level step (`agent-reach install --env=auto --system`) is printed, not run. It installs system dependencies and platform logins, so run it only after you decide to. Details are in the Setup section of `SKILL.md`.

## Usage

```bash
ladder.py search "query" --max 5        # TITLE<TAB>URL
ladder.py read URL ... | @urls.txt      # URL<TAB>STATUS<TAB>RUNG<TAB>TITLE<TAB>BODY
ladder.py read URL --from cdp           # start at the logged-in Chrome rung
```

Blocked means: HTTP 401/403/429/503, a body under 500 characters, or a captcha / login / cookie-wall marker in the first 3,000 characters.

## Builds on

- [Jina Reader](https://jina.ai/reader/): URL to Markdown
- [Exa](https://exa.ai/) via [mcporter](https://github.com/openclaw/mcporter): neural search, free MCP tier
- [TinyFish](https://tinyfish.ai/) via the `tinyfish` CLI (`npm i -g @tiny-fish/cli`): free search and fetch (rate-limited), needs `TINYFISH_API_KEY`
- [camofox-browser](https://github.com/jo-inc/camofox-browser): anti-detect Firefox REST service
- [web-access](https://github.com/eze-is/web-access): CDP proxy into your Chrome
- [Agent-Reach](https://github.com/Panniantong/Agent-Reach): platform backends (X, Reddit, YouTube, ...)

## Tests

```bash
python3 -m pytest -q tests
```

Python 3.10+ standard library only. MIT license.
