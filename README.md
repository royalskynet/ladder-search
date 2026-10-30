# ladder-search

A self-contained Claude Code plugin that routes every web task to the right tool: search lanes, a page-reading ladder, [Agent-Reach](https://github.com/Panniantong/Agent-Reach) for named platforms, and [web-access](https://github.com/eze-is/web-access) for logged-in or interactive browsing. It contains no code from either project. It calls them as dependencies and decides which one handles each request.

Agents tend to call `WebFetch` once and give up when the page is blocked. ladder-search runs a fixed procedure instead:
search in parallel, read each page on the cheapest rung that works, and move up one rung only when the page is blocked.

```
search   host WebSearch  +  Exa (mcporter)  ->  TinyFish  ->  ddgs fallback
read     jina (r.jina.ai)  ->  tinyfish (TinyFish Fetch)  ->  camofox (anti-detect Firefox)  ->  cdp (your logged-in Chrome via web-access)
platform  X / Reddit / YouTube / Bilibili / Xiaohongshu / Weibo / Xueqiu / GitHub  ->  agent-reach
interact  login, clicks, forms, or 1-3 failed twice  ->  web-access (CDP)
```

Each rung is tried at most once per URL. Unreachable rungs are skipped. A page blocked at the top rung is reported as a gap (exit 5), never retried in a loop.

## Install

As a plugin (skill + a `UserPromptSubmit` hook that injects the rule on web-lookup prompts):

```
/plugin marketplace add royalskynet/ladder-search
/plugin install ladder-search@ladder-search
```

Skill only (no hook; the model decides from the description when to use it):

```bash
git clone https://github.com/royalskynet/ladder-search ~/.claude/skills/ladder-search
```

Then check what is available:

```bash
python3 ~/.claude/skills/ladder-search/scripts/ladder.py doctor
```

Only Jina is required. It needs no key or install. To install the rest (Exa search, Agent-Reach, web-access):

```bash
scripts/setup.sh            # check only: lists what is missing and the commands it would run
scripts/setup.sh --apply    # install, user-level only, no sudo
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
