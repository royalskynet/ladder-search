#!/usr/bin/env python3
"""ladder-search: find sources, then read pages, escalating one rung only when the page is blocked.

Usage:
    ladder.py search "<query>" [--max N]          # Exa (via mcporter) -> TinyFish -> ddgs fallback
    ladder.py read <URL | @urls.txt> ... [--from jina|tinyfish|camofox|cdp] [--max-chars N]
    ladder.py doctor                              # which rungs are usable right now

Read ladder (each rung tried at most once per URL, never the same rung twice):
    1. jina     https://r.jina.ai/<URL>                      static, cheap, no browser
    2. tinyfish TinyFish Fetch (tinyfish CLI)                remote render, no local browser
    3. camofox  anti-detect Firefox REST (CAMOFOX_URL)        anti-bot, no login
    4. cdp      web-access cdp-proxy (CDP_PROXY_URL)          your logged-in Chrome
A rung is skipped when its service is not reachable. Escalation happens only when the
previous rung was blocked (401/403/429/503, body < 500 chars, or a captcha/login marker).

Output
    search: TITLE<TAB>URL                         (one hit per line)
    read:   URL<TAB>STATUS<TAB>RUNG<TAB>TITLE<TAB>BODY   (BODY newlines -> spaces)
            STATUS = 200 | 200:TRUNCATED | BLOCKED:<reason> | EMPTY | ERR | <http code>
Exit: 0 all ok, 2 some URL failed / no hits, 3 no rung/backend usable, 5 still blocked at top rung.

Env: CAMOFOX_URL (default http://localhost:9377), CDP_PROXY_URL (default http://localhost:3456),
     JINA_API_KEY (optional, raises Jina rate limit), SSL_CERT_FILE (MITM proxies),
     TINYFISH_API_KEY (read by the tinyfish CLI; or run `tinyfish auth set`),
     LADDER_NO_CDP=1 (drop the cdp rung; bots use scripts/ladder-bot, which sets it).
Only the Python standard library is used.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

JINA = "https://r.jina.ai/"
CAMOFOX = os.environ.get("CAMOFOX_URL", "http://localhost:9377").rstrip("/")
CDP = os.environ.get("CDP_PROXY_URL", "http://localhost:3456").rstrip("/")
RUNGS = ("jina", "tinyfish", "camofox", "cdp")
REMOTE_RUNGS = ("jina", "tinyfish")  # stateless HTTP services: run in parallel
MAX_CHARS = 20000
READ_TIMEOUT = 30
SEARCH_TIMEOUT = 60
JINA_WORKERS = 4  # ponytail: keyless Jina allows ~20 RPM; raise with JINA_API_KEY
CDP_SETTLE = 10.0  # seconds to wait for a dynamic page to replace a loading/verify shell
SAME_DOMAIN_GAP = 3.0

BLOCKED_STATUSES = (401, 403, 429, 503)
BLOCKED_MARKER_RE = re.compile(
    r"just a moment|attention required|enable javascript|access denied|captcha|"
    r"verify you are human|please log in|sign in to continue|accept all cookies|"
    r"datadome|akamai|incapsula|perimeterx",
    re.IGNORECASE,
)
_JINA_HEADERS = ("Title:", "URL Source:", "Published Time:", "Warning:", "Markdown Content:")
_READ_JS = "({title: document.title, body: document.body ? document.body.innerText : ''})"


def block_reason(status: int, body: str) -> str | None:
    """Blocked page? Order is fixed: status code -> short body -> wall marker."""
    if status in BLOCKED_STATUSES:
        return "status"
    if len(body) < 500:
        return "short"
    if BLOCKED_MARKER_RE.search(body[:3000]):
        return "marker"
    return None


def _ssl_context() -> ssl.SSLContext | None:
    ca = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if not ca or not os.path.exists(ca):
        return None
    try:
        return ssl.create_default_context(cafile=ca)
    except (OSError, ssl.SSLError):
        return None


def http(method: str, url: str, body: bytes | None = None, timeout: float = 60,
         headers: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read().decode("utf-8", "replace"), exc.code
    except Exception as exc:  # noqa: BLE001 - connection-level failure
        return 0, f"{type(exc).__name__}: {str(exc)[:300]}"
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


# ---------- rung availability ----------

def camofox_up() -> bool:
    sc, p = http("GET", CAMOFOX + "/health", timeout=3)
    return sc == 200 and isinstance(p, dict) and p.get("ok") is True


def _tinyfish() -> str | None:
    return shutil.which("tinyfish") or next(
        (c for c in ("/opt/homebrew/bin/tinyfish", os.path.expanduser("~/.local/bin/tinyfish")) if os.access(c, os.X_OK)), None)


def tinyfish_up() -> bool:
    return _tinyfish() is not None


def cdp_up() -> bool:
    sc, p = http("GET", CDP + "/health", timeout=3)
    return sc == 200 and isinstance(p, dict) and p.get("connected") is True


# ---------- rungs: each returns (status, title, body) ----------

def jina(url: str) -> tuple[str, str, str]:
    headers = {"User-Agent": "ladder-search/1.0", "Accept": "text/plain"}
    key = os.environ.get("JINA_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    sc, text = http("GET", JINA + url, timeout=READ_TIMEOUT, headers=headers)
    if sc == 0:
        return "ERR", "", str(text)
    if sc in BLOCKED_STATUSES:
        return "BLOCKED:status", "", ""
    if sc != 200:
        return str(sc), "", ""
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    title, lines = "", []
    for line in text.splitlines():
        s = line.strip()
        if not lines and s.startswith("Title:"):
            title = s[len("Title:"):].strip()
            continue
        if not lines and (not s or s.startswith(_JINA_HEADERS)):
            continue
        lines.append(line)
    return _classify(" ".join(lines), title)


def _tinyfish_json(args: list[str]) -> tuple[object, str]:
    tf = _tinyfish()
    if tf is None:
        return None, "tinyfish CLI not found"
    try:
        p = subprocess.run([tf, *args], capture_output=True, text=True, timeout=READ_TIMEOUT + 15)
    except subprocess.TimeoutExpired:
        return None, "tinyfish timeout"
    try:
        return json.loads(p.stdout), ""
    except ValueError:
        return None, (p.stderr or p.stdout).strip()[:300]


def tinyfish(url: str) -> tuple[str, str, str]:
    data, err = _tinyfish_json(["fetch", "content", "get", url, "--format", "markdown",
                                "--per-url-timeout-ms", str(READ_TIMEOUT * 1000)])
    return parse_tinyfish_fetch(data) if data is not None else ("ERR", "", err)


def parse_tinyfish_fetch(data: object) -> tuple[str, str, str]:
    """{"results":[{title,text}], "errors":[{url,error}]} for one URL -> (status, title, body)."""
    if not isinstance(data, dict):
        return "ERR", "", str(data)[:300]
    res = data.get("results") or []
    if res and isinstance(res[0], dict):
        return _classify(str(res[0].get("text") or ""), str(res[0].get("title") or ""))
    errs = data.get("errors") or [{}]
    return "ERR", "", str(errs[0].get("error", "no result") if isinstance(errs[0], dict) else errs[0])


def camofox(url: str) -> tuple[str, str, str]:
    user = "ladder-search"
    sc, tab = http("POST", CAMOFOX + "/tabs", json.dumps({"userId": user, "sessionKey": "ladder", "url": url}).encode(),
                   headers={"Content-Type": "application/json"})
    if sc != 200 or not isinstance(tab, dict) or "tabId" not in tab:
        return "ERR", "", str(tab)[:300]
    tid = tab["tabId"]
    try:
        http("POST", f"{CAMOFOX}/tabs/{tid}/wait", json.dumps({"userId": user, "timeout": 8000, "waitForNetwork": True}).encode(),
             headers={"Content-Type": "application/json"})
        _, snap = http("GET", f"{CAMOFOX}/tabs/{tid}/snapshot?userId={user}")
        body = snap.get("snapshot", "") if isinstance(snap, dict) else ""
        _, ev = http("POST", f"{CAMOFOX}/tabs/{tid}/evaluate", json.dumps({"userId": user, "expression": "document.title"}).encode(),
                     headers={"Content-Type": "application/json"})
        title = str(ev.get("result") or "") if isinstance(ev, dict) else ""
        return _classify(body, title)
    finally:
        http("DELETE", f"{CAMOFOX}/tabs/{tid}?userId={user}")


def cdp(url: str) -> tuple[str, str, str]:
    """Open a background tab in the web-access Chrome, wait for real content, read innerText, close."""
    sc, res = http("POST", CDP + "/new", url.encode())
    if sc != 200 or not isinstance(res, dict) or "targetId" not in res:
        return "ERR", "", str(res)[:300]
    tid = urllib.parse.quote(res["targetId"])
    try:
        deadline = time.monotonic() + CDP_SETTLE
        while True:
            _, ev = http("POST", f"{CDP}/eval?target={tid}", _READ_JS.encode())
            val = ev.get("value") if isinstance(ev, dict) else None
            title, body = (val.get("title", ""), val.get("body", "")) if isinstance(val, dict) else ("", "")
            # /new returns at load; SPAs and verify shells may still swap content in
            if block_reason(200, body) is None or time.monotonic() >= deadline:
                return _classify(body, title)
            time.sleep(1.5)
    finally:
        http("GET", f"{CDP}/close?target={tid}")


def _classify(body: str, title: str) -> tuple[str, str, str]:
    body = body.replace("\r", " ").replace("\t", " ").replace("\n", " ").strip()
    reason = block_reason(200, body)
    if not body:
        return "EMPTY", title, ""
    if reason:
        return f"BLOCKED:{reason}", title, body[:200]
    return ("200:TRUNCATED" if len(body) > MAX_CHARS else "200"), title, body[:MAX_CHARS]


RUNG_FN = {"jina": jina, "tinyfish": tinyfish, "camofox": camofox, "cdp": cdp}
RUNG_UP = {"jina": lambda: True, "tinyfish": tinyfish_up, "camofox": camofox_up, "cdp": cdp_up}


# ---------- commands ----------

def read(urls: list[str], start: str = "jina") -> int:
    rungs = list(RUNGS[RUNGS.index(start):])
    if os.environ.get("LADDER_NO_CDP") == "1":  # bots (OpenBot etc.) must never reach the user's logged-in Chrome
        rungs = [r for r in rungs if r != "cdp"]
    up = {r: RUNG_UP[r]() for r in rungs}
    if not any(up.values()):
        for u in urls:
            print(f"{u}\tERR\t-\t\tno rung reachable (run: ladder.py doctor)")
        return 3
    results: dict[int, tuple[str, str, str, str]] = {}
    trail: dict[int, list[str]] = {}  # every rung tried per URL; a failure row shows them all, so a reader
    pending = list(range(len(urls)))   # never mistakes "last rung" for "only rung" (10-30 OpenBot misreport)
    for rung in rungs:
        if not up[rung] or not pending:
            continue
        if rung in REMOTE_RUNGS:
            with ThreadPoolExecutor(max_workers=JINA_WORKERS) as ex:
                got = list(ex.map(lambda i: RUNG_FN[rung](urls[i]), pending))
        else:  # browser rungs: serial, spaced per domain, to stay polite and avoid tab storms
            got, last = [], {}
            for i in pending:
                host = urllib.parse.urlsplit(urls[i]).netloc
                if host in last:
                    time.sleep(max(0.0, SAME_DOMAIN_GAP - (time.monotonic() - last[host])))
                got.append(RUNG_FN[rung](urls[i]))
                last[host] = time.monotonic()
        nxt = []
        for i, (status, title, body) in zip(pending, got):
            trail.setdefault(i, []).append(f"{rung}={status}" + (f" {body[:80]}" if status == "ERR" else ""))
            results[i] = (status, rung, title, body)
            if status.startswith("BLOCKED") or status in ("EMPTY", "ERR"):
                nxt.append(i)
        pending = nxt
    rc = 0
    for i, u in enumerate(urls):
        status, rung, title, body = results.get(i, ("ERR", "-", "", "no rung reachable"))
        if not status.startswith("200") and i in trail:
            body = "tried " + " > ".join(trail[i]) + (f" | {body}" if body else "")
        print(f"{u}\t{status}\t{rung}\t{title}\t{body}")
        if status.startswith("BLOCKED"):
            rc = max(rc, 5)
        elif not status.startswith("200"):
            rc = max(rc, 2)
    return rc


def _mcporter() -> str | None:
    hit = shutil.which("mcporter")
    if hit:
        return hit
    for pat in ("~/.nvm/versions/node/*/bin/mcporter", "~/.local/bin/mcporter", "~/.npm-global/bin/mcporter"):
        for c in sorted(glob.glob(os.path.expanduser(pat)), reverse=True):
            if os.access(c, os.X_OK):
                return c
    return None


DDGS_SNIPPET = ("import json,sys; from ddgs import DDGS; "
                "print(json.dumps(DDGS().text(sys.argv[1], max_results=int(sys.argv[2]))))")


def _exa(query: str, n: int) -> list[tuple[str, str]] | None:
    mc = _mcporter()
    if mc is None:
        return None
    q = query.replace('"', "'")
    for wait in (3, 8, None):
        try:
            p = subprocess.run([mc, "call", f'exa.web_search_exa(query: "{q}", numResults: {n})'],
                               capture_output=True, text=True, timeout=SEARCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None
        limited = p.returncode != 0 and ("429" in p.stderr or "rate limit" in p.stderr)
        if not limited or wait is None:
            break
        time.sleep(wait)
    hits = parse_exa(p.stdout, n)
    if not hits:
        # rate-limit/error text has no Title:/URL: lines either; cascade to TinyFish
        return None
    return hits


def parse_exa(text: str, n: int) -> list[tuple[str, str]]:
    hits, seen, title = [], set(), ""
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("Title:"):
            title = s[len("Title:"):].strip()
        elif s.startswith("URL:"):
            u = s[len("URL:"):].strip()
            if u.startswith("http") and u not in seen:
                seen.add(u)
                hits.append((title, u))
            title = ""
        if len(hits) >= n:
            break
    return hits


def _tinyfish_search(query: str, n: int) -> list[tuple[str, str]] | None:
    if _tinyfish() is None:
        return None
    data, _ = _tinyfish_json(["search", "query", query])
    return parse_tinyfish_search(data, n)


def parse_tinyfish_search(data: object, n: int) -> list[tuple[str, str]] | None:
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return None
    hits = [(str(r.get("title", "")), r["url"]) for r in data["results"]
            if isinstance(r, dict) and str(r.get("url", "")).startswith("http")]
    return hits[:n]


def _ddgs(query: str, n: int) -> list[tuple[str, str]] | None:
    uvx = shutil.which("uvx") or os.path.expanduser("~/.local/bin/uvx")
    if not os.access(uvx, os.X_OK):
        return None
    try:
        p = subprocess.run([uvx, "--from", "ddgs", "python", "-c", DDGS_SNIPPET, query, str(n)],
                           capture_output=True, text=True, timeout=SEARCH_TIMEOUT)
        rows = json.loads(p.stdout or "null")
    except (subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    return [(r.get("title", ""), r["href"]) for r in rows if str(r.get("href", "")).startswith("http")]


def search(query: str, n: int) -> int:
    hits = _exa(query, n)
    if hits is None:
        print("[ladder-search] Exa unavailable (mcporter missing or failed); trying TinyFish", file=sys.stderr)
        hits = _tinyfish_search(query, n)
    if hits is None:
        print("[ladder-search] TinyFish unavailable (tinyfish CLI missing or failed); trying ddgs", file=sys.stderr)
        hits = _ddgs(query, n)
    if hits is None:
        print("[ladder-search] no search backend; use the host WebSearch tool", file=sys.stderr)
        return 3
    for t, u in hits:
        print(f"{t}\t{u}")
    return 0 if hits else 2


def doctor() -> int:
    rows = [
        ("search:exa", "ok" if _mcporter() else "off", "mcporter on PATH (npm i -g mcporter; mcporter config add exa --url https://mcp.exa.ai/mcp)"),
        ("search:tinyfish", "ok" if tinyfish_up() else "off", "tinyfish CLI (npm i -g @tiny-fish/cli; tinyfish auth set)"),
        ("search:ddgs", "ok" if (shutil.which("uvx") or os.access(os.path.expanduser("~/.local/bin/uvx"), os.X_OK)) else "off", "uvx on PATH"),
        ("read:jina", "ok", JINA),
        ("read:tinyfish", "ok" if tinyfish_up() else "off", "tinyfish fetch content get"),
        ("read:camofox", "ok" if camofox_up() else "off", CAMOFOX + "/health"),
        ("read:cdp", "ok" if cdp_up() else "off", CDP + "/health (web-access skill + Chrome with --remote-debugging-port)"),
        ("platforms", "ok" if os.path.isfile(os.path.expanduser("~/.claude/skills/agent-reach/SKILL.md")) else "off", "agent-reach skill"),
    ]
    for name, st, how in rows:
        print(f"{name:<15} {st:<3}  {how}")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "doctor":
        return doctor()
    if cmd == "search":
        n = 5
        if "--max" in rest:
            i = rest.index("--max")
            n = int(rest[i + 1])
            rest = rest[:i] + rest[i + 2:]
        if not rest:
            print("usage: ladder.py search \"<query>\" [--max N]", file=sys.stderr)
            return 2
        return search(" ".join(rest), n)
    if cmd == "read":
        global MAX_CHARS
        start = "jina"
        urls: list[str] = []
        it = iter(rest)
        for a in it:
            if a == "--from":
                start = next(it, "")
                if start not in RUNGS:
                    print(f"usage: --from {'|'.join(RUNGS)}", file=sys.stderr)
                    return 2
            elif a == "--max-chars":
                MAX_CHARS = int(next(it, MAX_CHARS))
            elif a.startswith("@"):
                with open(a[1:], encoding="utf-8") as fh:
                    urls += [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
            else:
                urls.append(a)
        if not urls:
            print("usage: ladder.py read <URL | @urls.txt> ...", file=sys.stderr)
            return 2
        return read(urls, start)
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
