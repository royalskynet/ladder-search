"""Ladder intent: escalate one rung only when blocked, each rung at most once per URL,
skip unreachable rungs, keep output in input order, and report a top-rung block as exit 5."""
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ladder  # noqa: E402

GOOD = "x" * 600


def _run(fns, up, urls, start=ladder.RUNGS[0]):
    calls = []
    up = {**{r: False for r in ladder.RUNGS}, **up}  # rungs a test does not name are down, never called for real

    def wrap(name):
        def f(url):
            calls.append((name, url))
            return fns[name](url)
        return f

    with mock.patch.dict(ladder.RUNG_FN, {k: wrap(k) for k in fns}), \
         mock.patch.dict(ladder.RUNG_UP, {k: (lambda v=v: v) for k, v in up.items()}), \
         mock.patch.object(ladder, "SAME_DOMAIN_GAP", 0.0), \
         mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        rc = ladder.read(urls, start)
    return rc, [l.split("\t") for l in out.getvalue().splitlines()], calls


def test_block_reason_order():
    assert ladder.block_reason(403, GOOD) == "status"
    assert ladder.block_reason(200, "short") == "short"
    assert ladder.block_reason(200, "Just a moment..." + GOOD) == "marker"
    assert ladder.block_reason(200, GOOD) is None


def test_ok_page_never_escalates():
    fns = {"jina": lambda u: ("200", "t", GOOD), "camofox": lambda u: 1 / 0, "cdp": lambda u: 1 / 0}
    rc, rows, calls = _run(fns, {"jina": True, "camofox": True, "cdp": True}, ["https://a.test/"])
    assert rc == 0 and rows[0][2] == "jina"
    assert calls == [("jina", "https://a.test/")]


def test_blocked_climbs_each_rung_once_and_skips_down_rungs():
    fns = {
        "jina": lambda u: ("BLOCKED:marker", "", ""),
        "camofox": lambda u: 1 / 0,  # unreachable, must not be called
        "cdp": lambda u: ("200", "t", GOOD),
    }
    rc, rows, calls = _run(fns, {"jina": True, "camofox": False, "cdp": True}, ["https://a.test/"])
    assert rc == 0 and rows[0][1:3] == ["200", "cdp"]
    assert calls == [("jina", "https://a.test/"), ("cdp", "https://a.test/")]


def test_still_blocked_at_top_is_exit_5_and_order_kept():
    fns = {"jina": lambda u: ("200", "t", GOOD) if "ok" in u else ("BLOCKED:status", "", ""),
           "camofox": lambda u: ("BLOCKED:marker", "", ""), "cdp": lambda u: ("BLOCKED:short", "", "")}
    urls = ["https://wall.test/", "https://ok.test/"]
    rc, rows, calls = _run(fns, {"jina": True, "camofox": True, "cdp": True}, urls)
    assert rc == 5
    assert [r[0] for r in rows] == urls
    assert rows[0][1:3] == ["BLOCKED:short", "cdp"]
    assert sum(1 for c in calls if c[1] == "https://wall.test/") == 3
    assert rows[0][4] == "tried jina=BLOCKED:status > camofox=BLOCKED:marker > cdp=BLOCKED:short", rows[0][4]
    assert rows[1][4] == GOOD, "a page that reads fine keeps its body untouched"


def test_err_trail_keeps_the_error_text():
    fns = {"jina": lambda u: ("ERR", "", "HTTP 451"), "tinyfish": lambda u: ("ERR", "", "rate_limited")}
    rc, rows, _ = _run(fns, {"jina": True, "tinyfish": True}, ["https://a.test/"])
    assert rc == 2 and rows[0][4].startswith("tried tinyfish=ERR rate_limited > jina=ERR HTTP 451"), rows[0][4]


def test_from_cdp_skips_lower_rungs():
    fns = {"jina": lambda u: 1 / 0, "camofox": lambda u: 1 / 0, "cdp": lambda u: ("200", "t", GOOD)}
    rc, rows, _ = _run(fns, {"jina": True, "camofox": True, "cdp": True}, ["https://a.test/"], start="cdp")
    assert rc == 0 and rows[0][2] == "cdp"


def test_parse_exa():
    text = "Title: A\nURL: https://a.test/\nTitle: dup\nURL: https://a.test/\nTitle: B\nURL: https://b.test/\n"
    assert ladder.parse_exa(text, 5) == [("A", "https://a.test/"), ("B", "https://b.test/")]


def _hint(prompt):
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "hint.py")],
                       input=json.dumps({"prompt": prompt}), capture_output=True, text=True, check=True)
    return p.stdout


def test_hint_fires_on_web_task_only():
    assert "[ladder-search]" in json.loads(_hint("幫我讀 https://example.com"))["hookSpecificOutput"]["additionalContext"]
    assert _hint("把這個函式改名") == ""


def test_tinyfish_rung_is_first_then_jina():
    fns = {"tinyfish": lambda u: ("BLOCKED:short", "", ""), "jina": lambda u: ("200", "t", GOOD),
           "camofox": lambda u: 1 / 0, "cdp": lambda u: 1 / 0}
    rc, rows, calls = _run(fns, {"jina": True, "tinyfish": True, "camofox": True, "cdp": True}, ["https://a.test/"])
    assert rc == 0 and rows[0][1:3] == ["200", "jina"]
    assert [c[0] for c in calls] == ["tinyfish", "jina"]


def test_tinyfish_unavailable_starts_at_jina():
    fns = {"tinyfish": lambda u: 1 / 0, "jina": lambda u: ("200", "t", GOOD), "camofox": lambda u: 1 / 0,
           "cdp": lambda u: 1 / 0}
    rc, rows, calls = _run(fns, {"tinyfish": False, "jina": True, "camofox": True, "cdp": True}, ["https://a.test/"])
    assert rc == 0 and rows[0][1:3] == ["200", "jina"]
    assert calls == [("jina", "https://a.test/")]


def test_parse_tinyfish_fetch():
    ok = {"results": [{"title": "T", "text": "line\n" + GOOD}], "errors": []}
    assert ladder.parse_tinyfish_fetch(ok)[:2] == ("200", "T")
    assert ladder.parse_tinyfish_fetch({"results": [], "errors": [{"url": "u", "error": "target_unreachable"}]}) == \
        ("ERR", "", "target_unreachable")
    assert ladder.parse_tinyfish_fetch("not json")[0] == "ERR"


def test_parse_tinyfish_search():
    data = {"results": [{"title": "A", "url": "https://a.test/"}, {"title": "x", "url": "ftp://no"},
                        {"title": "B", "url": "https://b.test/"}]}
    assert ladder.parse_tinyfish_search(data, 5) == [("A", "https://a.test/"), ("B", "https://b.test/")]
    assert ladder.parse_tinyfish_search(data, 1) == [("A", "https://a.test/")]
    assert ladder.parse_tinyfish_search({"error": "auth"}, 5) is None


def test_no_cdp_env_never_touches_logged_in_chrome():
    blocked = lambda u: ("BLOCKED:status", "", "")
    with mock.patch.dict("os.environ", {"LADDER_NO_CDP": "1"}):
        rc, rows, calls = _run({"jina": blocked, "cdp": lambda u: ("200", "", GOOD)},
                               {"jina": True, "cdp": True}, ["https://a.example/1"])
    assert ("cdp", "https://a.example/1") not in calls, calls
    assert rc != 0


def test_short_page_is_not_a_block():
    """Length alone must not trigger a rung climb or a false gap at the top rung."""
    # A legitimately small page: one sentence, no markup, no wall marker.
    small = "Release 3.50.0 shipped on 2025-05-29 with a new lock timeout interface."
    assert ladder.block_reason(200, small) is None
    # The precise allowances: bare JSON, Unicode-readable text, and readable redirect text.
    assert ladder.block_reason(200, '{"ok":true}') is None
    assert ladder.block_reason(200, "這一頁說明版本更新修正了登入逾時與鎖定問題。") is None
    assert ladder.block_reason(200, "이 페이지는 로그인 시간 초과와 잠금 문제가 수정되었다고 설명합니다.") is None
    assert ladder.block_reason(200, '<html><body>Redirecting to the release page.</body></html>') is None
    # A JS/login shell or content-free stub of the same size still counts as blocked.
    assert ladder.block_reason(200, "<html><script>challenge()</script></html>") == "short"
    assert ladder.block_reason(200, "<html><head><title>Welcome to the wonderful example site</title></head><body><script>challenge()</script></body></html>") == "short"
    assert ladder.block_reason(200, '<html><body>Redirecting...</body></html>') == "short"
    assert ladder.block_reason(200, "x") == "short"
    assert ladder.block_reason(200, "Just a moment...") == "marker"
    assert ladder.block_reason(403, GOOD) == "status"
    # End to end: a small page stops at the first rung instead of climbing.
    rc, rows, calls = _run({"jina": lambda u: ("200", "ok", small), "camofox": lambda u: ("200", "", GOOD)},
                           {"jina": True, "camofox": True}, ["https://a.example/p"])
    assert rc == 0 and calls == [("jina", "https://a.example/p")], (rc, calls)


def test_connection_failure_payload_remains_plain_text():
    """Backwards-compatible contract: connection errors are plain text, not dict reprs."""
    with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        sc, payload = ladder.http("GET", "http://service.invalid/health", timeout=0.01)
    assert sc == 0
    assert isinstance(payload, str)
    assert payload == "OSError: connection refused"


def test_jina_html_is_reduced_to_text():
    """Jina sometimes returns raw HTML under 'Markdown Content:'."""
    page = ("Title: \n\nURL Source: https://a.example/\n\nMarkdown Content:\n"
            "<!DOCTYPE html><html><head><title>Real Title</title><style>b{}</style></head>"
            "<body><div>Hello world, this is the actual sentence of the page.</div>"
            "<script>var x=1</script></body></html>")
    body = page
    with mock.patch.object(ladder, "http", return_value=(200, body)):
        status, title, text = ladder.jina("https://a.example/")
    assert status.startswith("200"), status
    assert title == "Real Title", title
    assert "<div" not in text and "var x=1" not in text, text[:200]
    assert "Hello world" in text


def test_camofox_idle_browser_is_still_available():
    """Lazy lifecycle: browserConnected:false can mean intentional idle, not down."""
    idle = {"ok": True, "browserConnected": False, "browserRunning": False, "activeTabs": 0}
    with mock.patch.object(ladder, "http", return_value=(200, idle)):
        assert ladder.camofox_up() is True
    with mock.patch.object(ladder, "http", return_value=(200, {"ok": True, "browserConnected": True})):
        assert ladder.camofox_up() is True


def test_camofox_genuine_error_is_unavailable():
    """Only non-200 health or ok:false disables the rung."""
    down = {"ok": False, "browserRunning": False, "reason": "missing_binary"}
    with mock.patch.object(ladder, "http", return_value=(503, down)):
        assert ladder.camofox_up() is False
    with mock.patch.object(ladder, "http", return_value=(200, {"ok": False, "browserConnected": False})):
        assert ladder.camofox_up() is False
    with mock.patch.object(ladder, "http", return_value=(0, "URLError: connection refused")):
        assert ladder.camofox_up() is False
