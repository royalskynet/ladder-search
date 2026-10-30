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


def _run(fns, up, urls, start="jina"):
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
    assert rc == 2 and rows[0][4].startswith("tried jina=ERR HTTP 451 > tinyfish=ERR rate_limited"), rows[0][4]


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


def test_tinyfish_rung_sits_between_jina_and_camofox():
    fns = {"jina": lambda u: ("BLOCKED:short", "", ""), "tinyfish": lambda u: ("200", "t", GOOD),
           "camofox": lambda u: 1 / 0, "cdp": lambda u: 1 / 0}
    rc, rows, calls = _run(fns, {"jina": True, "tinyfish": True, "camofox": True, "cdp": True}, ["https://a.test/"])
    assert rc == 0 and rows[0][1:3] == ["200", "tinyfish"]
    assert [c[0] for c in calls] == ["jina", "tinyfish"]


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
