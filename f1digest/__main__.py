"""CLI:  python -m f1digest [run|fetch|backfill|check-feeds|rebuild-site|test-email|serve|status]"""
import argparse
import os
import subprocess
import sys
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import feeds, mailer, pipeline, render, store, watchdog
from .llm import LLM

ROOT = Path(__file__).resolve().parent.parent


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def publish(msg: str):
    if not (ROOT / ".git").exists():
        print("publish: not a git repo, skipping"); return
    try:
        subprocess.run(["git", "add", "docs"], cwd=ROOT, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode:
            subprocess.run(["git", "commit", "-m", msg], cwd=ROOT, check=True)
            subprocess.run(["git", "push"], cwd=ROOT, check=True)
            print("published")
    except subprocess.CalledProcessError as e:
        print(f"publish failed: {e}", file=sys.stderr)
        return f"publish failed: {e}"


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run",
                    choices=["run", "fetch", "backfill", "check-feeds", "rebuild-site", "test-email", "serve", "status"])
    ap.add_argument("--force", action="store_true", help="rebuild today's digest even if it exists")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--fulltext", action="store_true", help="backfill: also read full articles (slow)")
    ap.add_argument("--port", type=int, default=8080, help="serve: port")
    return ap.parse_args()


def execute(args) -> str | None:
    """Run one command. Returns a problem description if something went wrong the watchdog should hear about."""
    cfg = tomllib.loads((ROOT / "config.toml").read_text())
    if args.cmd == "check-feeds":
        _, report = feeds.fetch_all(cfg["feeds"])
        for name, status in report:
            print(f"{name:16} {status}")
        return

    if args.cmd == "serve":  # private alternative to GitHub Pages: open http://<pi-ip>:PORT on your home network
        import functools
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(ROOT / "docs"))
        print(f"serving {ROOT / 'docs'} on port {args.port} (Ctrl+C to stop)")
        ThreadingHTTPServer(("0.0.0.0", args.port), handler).serve_forever()
        return

    conn = store.connect(ROOT / "data" / "f1.db")
    llm = LLM(cfg["ai"])
    tz = ZoneInfo(cfg["general"]["timezone"])
    today = datetime.now(tz).date()
    g, docs = cfg["general"], ROOT / "docs"

    if args.cmd == "status":
        q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        print(f"digests: {q('SELECT COUNT(*) FROM days')}  (latest {q('SELECT MAX(day) FROM days')}, built {q('SELECT MAX(built_at) FROM days')})")
        print(f"stories: {q('SELECT COUNT(*) FROM stories')}  articles: {q('SELECT COUNT(*) FROM articles')}  "
              f"pending emails: {len(pipeline.pending_days(conn, today))}")
        print(f"newest article seen: {q('SELECT MAX(COALESCE(seen_at, published)) FROM articles')}")
        bk = sorted((ROOT / "data" / "backups").glob("f1-*.db")) if (ROOT / "data" / "backups").exists() else []
        print(f"backups: {len(bk)} (latest {bk[-1].name if bk else 'none'})  db size: {(ROOT / 'data' / 'f1.db').stat().st_size // 1024} KB")
        print(f"model: {'online' if llm.available() else 'OFFLINE'}")
        return
    if args.cmd == "rebuild-site":
        render.write_site(docs, pipeline.archive(conn, today, cfg)); return
    if args.cmd == "test-email":
        s, h, t = render.email_bodies(today.isoformat(), [{
            "kind": "new", "headline": "Test story", "summary": "If you can read this, email works.",
            "category": "Test", "confidence": "Single source", "links": []}], cfg["site"]["url"])
        mailer.send(cfg["email"]["provider"], s, h, t); print("sent"); return

    arts, report = feeds.fetch_all(cfg["feeds"])
    for name, status in report:
        print(f"{name:16} {status}")
    cutoff = (today - timedelta(days=g["window_days"])).isoformat()
    arts = [a for a in arts if a["published"] >= cutoff]
    arts, dropped = feeds.filter_articles(arts, cfg)
    if dropped:
        print(f"filtered out {len(dropped)} articles (noise / off-topic)")
    print(f"ingested {pipeline.ingest(conn, arts, llm, cfg, 'published' if args.cmd == 'backfill' else 'now')} new articles "
          f"({'embeddings' if llm.available() else 'Ollama down: lexical fallback'})")
    ok = sum(1 for _, st in report if st.startswith("ok"))
    warning, problem = "", None
    if ok == 0:
        problem = "all news feeds failed"
    if ok < max(1, len(report) // 2):
        warning = f"Only {ok} of {len(report)} news feeds responded today, so some news may be missing."
        print("WARNING:", warning, file=sys.stderr)
    if not llm.available():
        warning = (warning + " " if warning else "") + "The local model was offline, so story matching and summaries were simpler than usual."
    if args.cmd == "fetch":  # cheap collection run (every few hours) so feeds can't roll over unseen
        return problem

    if args.cmd == "backfill":
        cfg["ai"]["fulltext"] = args.fulltext  # backfill is fast by default: headlines + snippets only
        first = conn.execute("SELECT MIN(published) FROM articles").fetchone()[0]
        d = datetime.fromisoformat(first).astimezone(tz).date() if first else today
        while d <= today:
            n = len(pipeline.build_digest(conn, d, llm, cfg)); print(f"{d}: {n} stories"); d += timedelta(days=1)
        conn.execute("UPDATE days SET emailed=1"); conn.commit()  # backfill never emails
    else:
        items = pipeline.build_digest(conn, today, llm, cfg, force=args.force)
        print(f"{today}: {len(items)} stories")
        if args.force:
            conn.execute("UPDATE days SET emailed=0 WHERE day=?", (today.isoformat(),)); conn.commit()

    pipeline.prune(conn, today, cfg)
    bk = cfg.get("backup", {})
    try:
        path = store.backup(conn, bk.get("dir") or ROOT / "data" / "backups", today.isoformat(), bk.get("keep", 7))
        print(f"backup: {path}")
    except Exception as e:  # noqa: BLE001  (e.g. USB stick unplugged: never block the digest)
        print(f"backup failed: {e}", file=sys.stderr)
    render.write_site(docs, pipeline.archive(conn, today, cfg))

    if args.cmd == "run" and not args.no_email:   # also retries any recent day whose email failed
        for day in pipeline.pending_days(conn, today):
            items = pipeline.load_day(conn, date.fromisoformat(day))
            if items or cfg["email"]["send_empty"]:
                try:
                    s, h, t = render.email_bodies(day, items, cfg["site"]["url"], warning if day == today.isoformat() else "")
                    mailer.send(cfg["email"]["provider"], s, h, t)
                    print(f"email sent for {day}")
                except Exception as e:  # noqa: BLE001
                    print(f"email failed for {day}: {e} (will retry next run)", file=sys.stderr)
                    problem = f"email failed for {day}: {str(e)[:200]}"
                    continue
            conn.execute("UPDATE days SET emailed=1 WHERE day=?", (day,)); conn.commit()
    if cfg["site"]["publish"]:
        problem = publish(f"digest {today}") or problem
    return problem


def main():
    args = parse_args()
    load_env()
    env = {"run": "HEALTHCHECK_URL", "fetch": "HEALTHCHECK_FETCH_URL"}.get(args.cmd)
    if not env:
        execute(args)
        return
    watchdog.ping(env, "start")
    try:
        problem = execute(args)
    except Exception as e:  # noqa: BLE001
        watchdog.ping(env, "fail", f"{type(e).__name__}: {e}")
        raise
    watchdog.ping(env, "fail" if problem else "", problem or "ok")
    if problem:
        print(f"PROBLEM: {problem}", file=sys.stderr)


if __name__ == "__main__":
    main()
