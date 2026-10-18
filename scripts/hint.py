#!/usr/bin/env python3
"""UserPromptSubmit hook: when the prompt is a web lookup, inject the ladder-search rule.

A skill description alone leaves the choice to the model, which often falls back to a
single WebFetch. This hook makes the rule appear on every matching turn instead.
Reads the hook JSON on stdin; prints nothing (no-op) when the prompt is not a web task.
"""
from __future__ import annotations

import json
import os
import re
import sys

TRIGGER = re.compile(
    r"https?://|上網|上网|網上|网上|網路上|网络上|搜尋|搜索|搜一下|查一下|找資料|找资料|網頁|网页|網站|网站|官網|官网|"
    r"網址|网址|抓網頁|抓网页|爬取|爬蟲|爬虫|新聞|新闻|登入後|登录后|"
    r"\bsearch\b|\bgoogle\b|look ?up|browse|web ?page|website|\bfetch\b|scrap(e|ing)|"
    r"推特|twitter|\bx\.com\b|\breddit\b|小紅書|小红书|b站|bilibili|youtube|微博|weibo|雪球|xueqiu|github",
    re.IGNORECASE,
)

WA_CHROME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wa-chrome")
LADDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ladder.py")
RULE = (
    "[ladder-search] Web lookup detected. Do not rely on a single WebFetch. "
    f"1) Find sources: host WebSearch + `python3 {LADDER} search \"<q>\"` in parallel. "
    "2) Read known URLs: `ladder.py read <url>...` (Jina first, auto-escalates to camofox, then your logged-in "
    "Chrome via web-access CDP, only when blocked). Use WebFetch only to extract a specific field; raw HTML / JSON-LD via curl. "
    "3) Named platforms (X/Reddit/YouTube transcripts/Bilibili/Xiaohongshu/Weibo/Xueqiu/GitHub): use the agent-reach skill. "
    f"4) Login state, dynamic rendering, anti-bot, clicking, or steps 1-3 failed twice on one target: use the web-access skill "
    f"(if its Chrome is not running: `{WA_CHROME} start`; sites logged into later need `{WA_CHROME} sync`). "
    "Never retry the same method on the same target; move one rung up."
)


def main() -> int:
    try:
        prompt = json.load(sys.stdin).get("prompt", "")
    except ValueError:
        return 0
    if not TRIGGER.search(prompt or ""):
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": RULE}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
