"""Multi-day simulation: no repeats, nothing missed, 7-day resurface, prune, and the audit-found gaps."""
import sys
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from f1digest import feeds, pipeline, render, store  # noqa: E402
from f1digest import llm as L  # noqa: E402
from f1digest.llm import LLM  # noqa: E402

cfg = tomllib.loads((Path(__file__).resolve().parent.parent / "config.toml").read_text())
cfg["general"]["timezone"] = "UTC"
cfg["ai"]["fulltext"] = False
llm = LLM({**cfg["ai"], "ollama_url": "http://127.0.0.1:1"})  # unreachable -> fallback paths
assert not llm.available()
D0 = date(2026, 11, 1)


def art(title, source, day, text="", hour=10):
    url = f"https://{source.lower().replace(' ', '')}.com/{abs(hash(title)) % 10**8}"
    d = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
    return {"id": feeds.article_id(url), "url": url, "title": title, "source": source,
            "published": d.isoformat(timespec="seconds"), "text": text}


def noon(day):
    return datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)


def digest(conn, day, c=cfg, model=llm):
    return pipeline.build_digest(conn, day, model, c, now=noon(day))


def run_day(conn, day, arts, c=cfg, model=llm):
    pipeline.ingest(conn, arts, model, c, seen="published")
    return digest(conn, day, c, model)


conn = store.connect(":memory:")
# ---- Day 1: same story from 3 outlets + one unrelated story
r = run_day(conn, D0, [
    art("Verstappen handed five-place grid penalty for engine change", "Autosport", D0, "Red Bull confirmed a new power unit."),
    art("Verstappen receives five-place grid penalty after engine change", "BBC Sport", D0),
    art("Red Bull's Verstappen hit with grid penalty for engine change", "RaceFans", D0),
    art("Mercedes unveils major floor upgrade for Mexico", "The Race", D0, "Mercedes brings a new floor."),
])
assert len(r) == 2 and r[0]["confidence"].startswith("Reported by 3"), r
print("day1 ok: 3 outlets merged into 1 story")

# ---- Day 2: late copy of same story + brand-new story -> only the new one
d1 = D0 + timedelta(days=1)
r = run_day(conn, d1, [
    art("Verstappen five-place grid penalty engine change, Red Bull confirms", "Sky Sports", d1),
    art("Hamilton extends Ferrari contract through 2028", "Autosport", d1),
])
assert [x["kind"] for x in r] == ["new"] and "Hamilton" in r[0]["headline"], r
assert digest(conn, d1) == r  # idempotent
print("day2 ok: late copy suppressed, only Hamilton shown")

# ---- Day 4: genuinely new coverage within 7 days -> compact follow-up, not a repeat
d3 = D0 + timedelta(days=3)
r = run_day(conn, d3, [art("Verstappen penalty engine change appeal rejected", "Crash.net", d3)])
assert len(r) == 1 and r[0]["kind"] == "followup" and r[0]["tier"] == "also", r
assert run_day(conn, D0 + timedelta(days=4), []) == []
print("day4 ok: follow-up shown once, story not repeated")

# ---- Day 10: >7 days after the story was shown, with fresh coverage -> full update
d9 = D0 + timedelta(days=9)
r = run_day(conn, d9, [art("Verstappen engine change penalty reignites Red Bull power unit debate", "Autosport", d9)])
assert len(r) == 1 and r[0]["kind"] == "update" and r[0]["tier"] == "top", r
print("day10 ok: resurfaced as update")

# ---- Cap: nothing dropped, overflow goes to the 'also' tier
c2 = store.connect(":memory:"); cfg2 = {**cfg, "general": {**cfg["general"], "max_items": 2}}
topics = ["Alpine confirms Gasly extension", "Williams tests new rear wing", "Sauber reveals livery launch date",
          "Pirelli announces tyre compounds Brazil", "Stewards investigate pit lane incident Albon"]
r = run_day(c2, D0, [art(t, "Autosport", D0) for t in topics], cfg2)
tiers = [x["tier"] for x in r]
assert len(r) == 5 and tiers.count("top") == 2 and tiers.count("also") == 3, r
print("cap ok: 5 stories, 2 full + 3 compact, none lost")

# ---- Catch-up after Pi offline
c3 = store.connect(":memory:")
pipeline.ingest(c3, [art("Norris wins thrilling Brazilian sprint", "BBC Sport", D0)], llm, cfg, seen="published")
digest(c3, D0 - timedelta(days=1))
r = digest(c3, D0 + timedelta(days=3))
assert len(r) == 1 and "Norris" in r[0]["headline"], r
print("catch-up ok: missed days covered")

# ============================ AUDIT-FOUND GAPS ============================
# GAP 1: article published days before we first saw it (feed outage / new feed) must still be delivered
c4 = store.connect(":memory:")
for i in range(5):
    digest(c4, D0 + timedelta(days=i))                                    # daily runs with nothing coming in
late = art("Alonso retires from Formula 1 after Brazil", "feedA", D0)      # published day 1...
pipeline.ingest(c4, [late], llm, cfg, seen=noon(D0 + timedelta(days=4)).replace(hour=14).isoformat())  # ...seen day 5
r = digest(c4, D0 + timedelta(days=5))
assert len(r) == 1 and "Alonso" in r[0]["headline"], r
print("GAP1 fixed: article first seen 4 days after publication is still delivered")


# GAP 2: embeddings alone must not merge two different driver stories
class FakeLLM(LLM):
    def available(self): return True
    def embed(self, texts): return [[1.0, 0.02 * (i % 2)] for i, _ in enumerate(texts)]  # ~identical vectors


fake = FakeLLM(cfg["ai"])
c5 = store.connect(":memory:")
pipeline.ingest(c5, [art("Norris wins the Sao Paulo sprint", "feedA", D0),
                     art("Piastri penalised five seconds in qualifying", "feedB", D0)], fake, cfg, seen="published")
assert c5.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 2
pipeline.ingest(c5, [art("Norris wins sprint at Sao Paulo, extends points lead", "feedC", D0)], fake, cfg, seen="published")
assert c5.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 2   # the real duplicate still merges
print("GAP2 fixed: different stories not merged on embeddings alone; true duplicates still merge")

# GAP 3: URLs that differ only by query id stay distinct; tracking params are ignored
assert feeds.article_id("https://site.com/?p=101") != feeds.article_id("https://site.com/?p=102")
assert feeds.article_id("https://site.com/a?utm_source=x&fbclid=1") == feeds.article_id("https://site.com/a")
print("GAP3 fixed: URL identity")

# GAP 4: failed email is retried
c6 = store.connect(":memory:")
run_day(c6, D0, [art("Albon extends Williams deal", "Autosport", D0)])
assert pipeline.pending_days(c6, D0 + timedelta(days=1)) == [D0.isoformat()]
c6.execute("UPDATE days SET emailed=1"); c6.commit()
assert pipeline.pending_days(c6, D0 + timedelta(days=1)) == []
print("GAP4 fixed: unsent emails are retried")

# GAP 5: summaries that invent names/numbers are rejected
src = "MAIN ARTICLE Verstappen was given a five-place grid penalty after Red Bull fitted a 4th power unit"
assert L.grounded("Verstappen takes grid penalty after Red Bull fitted a 4th power unit", src)
assert not L.grounded("Verstappen penalised, Horner says 7 places lost", src)
assert not L.grounded("Penalty follows a clash with Hamilton at the start", src)
print("GAP5 fixed: ungrounded summaries fall back to extractive text")

# ---- Ranking: a single-source "disqualified" scoop from a trusted outlet beats a 2-outlet fluff story
c8 = store.connect(":memory:"); cfg8 = {**cfg, "general": {**cfg["general"], "max_items": 1}}
r = run_day(c8, D0, [art("Sauber shares photo of new garage wall", "Crash.net", D0), art("Sauber posts garage wall photo online", "PlanetF1", D0),
                     art("Leclerc disqualified from Brazil qualifying", "Autosport", D0)], cfg8)
top = [x for x in r if x["tier"] == "top"]
assert len(top) == 1 and "Leclerc" in top[0]["headline"], r
print("ranking ok: trusted-source scoop outranks fluff")

# ---- Filters: noise by title; off-topic only from broad feeds
news = [{**art("How to watch the Brazilian GP: TV times", "Sky", D0), "broad": False},
        {**art("Local bakery wins award", "GoogleNews", D0), "broad": True},
        {**art("Verstappen leads practice", "GoogleNews", D0), "broad": True},
        {**art("Cooking tips", "Autosport", D0), "broad": False}]
kept, dropped = feeds.filter_articles(news, cfg)
assert [a["title"] for a in kept] == ["Verstappen leads practice", "Cooking tips"] and len(dropped) == 2
print("filters ok")

# ---- Backup: consistent, readable, rotates
import tempfile  # noqa: E402
bd = Path(tempfile.mkdtemp())
for i in range(10):
    store.backup(c8, bd, f"2026-11-{i + 1:02d}", keep=7)
files = sorted(bd.glob("f1-*.db"))
assert len(files) == 7 and files[0].name == "f1-2026-11-04.db"
assert store.connect(files[-1]).execute("SELECT COUNT(*) FROM entries").fetchone()[0] == len(r)
print("backup ok: snapshot readable, keeps newest 7")

# ---- Prune + render
pipeline.prune(conn, D0 + timedelta(days=45), cfg)
assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0
c7 = store.connect(":memory:")
r = run_day(c7, D0, [art("Alonso signs one-year Aston Martin extension", "Autosport", D0)])
days = pipeline.archive(c7, D0, cfg)
out = Path("/tmp/f1_site_test"); render.write_site(out, days)
s2, h2, t2 = render.email_bodies(D0.isoformat(), r + [{**r[0], "tier": "also"}], "", "Only 1 of 8 feeds responded")
assert "Also in the news" in h2 and "Only 1 of 8" in h2 and "+ 1 more" in s2 and (out / "index.html").stat().st_size > 3000
print("render + prune ok")
print("ALL TESTS PASSED")
