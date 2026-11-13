"""Watchdog pings: success, problem, crash, and no-URL no-op. Network and feeds are mocked."""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import f1digest.__main__ as cli  # noqa: E402
from f1digest import feeds, mailer, watchdog  # noqa: E402

now = datetime.now(timezone.utc)


def mk(t, src):
    u = f"https://{src}.com/{abs(hash(t)) % 10**8}"
    return {"id": feeds.article_id(u), "url": u, "title": t, "source": src, "broad": False,
            "published": (now - timedelta(minutes=30)).isoformat(timespec="seconds"), "text": "Detail. More detail."}


def run(cmd, fetch_result, send=lambda *a: None, hc="https://hc-ping.com/abc", fail_exc=None):
    calls = []
    tmp = Path(tempfile.mkdtemp()); shutil.copy(ROOT / "config.toml", tmp / "config.toml")  # fresh database per run
    env = {"F1_EMAIL_TO": "x@example.com", "RESEND_API_KEY": "k", "HEALTHCHECK_URL": hc, "HEALTHCHECK_FETCH_URL": hc + "-fetch"}
    if not hc:
        env.pop("HEALTHCHECK_URL"); env.pop("HEALTHCHECK_FETCH_URL")
    with mock.patch.dict(os.environ, env, clear=False), \
         mock.patch.object(sys, "argv", ["f1digest", cmd, "--no-email"] if cmd == "fetch" else ["f1digest", cmd]), \
         mock.patch.object(feeds, "fetch_all", side_effect=fail_exc) if fail_exc else mock.patch.object(feeds, "fetch_all", return_value=fetch_result), \
         mock.patch.object(mailer, "send", send), \
         mock.patch.object(watchdog.requests, "post", side_effect=lambda url, data=None, timeout=0: calls.append((url, data))), \
         mock.patch.object(cli, "ROOT", tmp):
        try:
            cli.main()
        except Exception as e:  # noqa: BLE001
            calls.append(("raised", str(e)))
    return calls


good = ([mk("Leclerc disqualified from Brazil qualifying", "Autosport")], [("A", "ok (1 items)")])

c = run("run", good)
assert [u for u, _ in c] == ["https://hc-ping.com/abc/start", "https://hc-ping.com/abc"], c
print("success: /start then success ping")

c = run("run", ([], [("A", "FAILED"), ("B", "FAILED")]))
assert c[-1][0] == "https://hc-ping.com/abc/fail" and b"all news feeds failed" in c[-1][1], c
print("all feeds down: /fail with reason")

def boom(*a): raise RuntimeError("smtp down")
c = run("run", good, send=boom)
assert c[-1][0].endswith("/fail") and b"email failed" in c[-1][1], c
print("email failure: /fail with reason")

c = run("run", None, fail_exc=RuntimeError("kaboom"))
assert c[-2][0].endswith("/fail") and c[-1][0] == "raised", c
print("crash: /fail sent, error still raised (systemd sees failure)")

c = run("fetch", good)
assert c[0][0] == "https://hc-ping.com/abc-fetch/start" and c[-1][0] == "https://hc-ping.com/abc-fetch", c
print("fetch uses its own check URL")

c = run("run", good, hc="")
assert c == [], c
print("no URL configured: silent no-op")
print("WATCHDOG TESTS PASSED")
