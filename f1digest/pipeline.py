"""Core logic: cluster articles into stories, then pick what is genuinely new for a given day."""
import json
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import feeds
from .llm import extractive

STOP = set("the a an of to in on at for and or is are was were be been with as by from that this it its his her their "
           "after before over under into out up down new says say said will would could has have had not but "
           "f1 formula one grand prix gp".split())
CLUSTER_DAYS = 14  # only match new articles against stories active in the last N days


def tokens(s: str) -> set[str]:
    s = re.sub(r"['’]s\b", "", s.lower())
    return {w for w in re.findall(r"[a-z0-9]+", s) if w not in STOP and len(w) > 2}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- ingest / cluster

def ingest(conn, articles: list[dict], llm, cfg: dict, seen: str = "now") -> int:
    """Store unseen articles and attach each to a story. `seen`: "now", "published" (backfill) or an ISO time (tests)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    known = {r[0] for r in conn.execute("SELECT id FROM articles")}
    new = sorted((a for a in articles if a["id"] not in known), key=lambda a: a["published"])
    new = list({a["id"]: a for a in new}.values())
    if not new:
        return 0
    embs = llm.embed([f"{a['title']}. {a['text'][:160]}" for a in new])
    ai = cfg["ai"]
    for i, a in enumerate(new):
        vec = embs[i] if embs else None
        sid, score, dup_thr = _match_story(conn, a, vec, ai)
        dup = 0
        if sid is None:
            cur = conn.execute("INSERT INTO stories(centroid, keywords, n_emb) VALUES(?,?,?)",
                               (json.dumps(vec) if vec else None, json.dumps(sorted(tokens(a["title"]))), 1 if vec else 0))
            sid = cur.lastrowid
        else:
            dup = 1 if score >= dup_thr else 0   # near-identical copy of what the story already says
            _update_story(conn, sid, a, vec)
        seen_at = a["published"] if seen == "published" else (now if seen == "now" else seen)
        conn.execute("INSERT INTO articles(id,story_id,url,title,source,published,text,dup,seen_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (a["id"], sid, a["url"], a["title"], a["source"], a["published"], a["text"], dup, seen_at))
    conn.commit()
    return len(new)


def _match_story(conn, a, vec, ai):
    since = (datetime.fromisoformat(a["published"]) - timedelta(days=CLUSTER_DAYS)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT s.id, s.centroid, s.keywords FROM stories s WHERE s.id IN "
        "(SELECT story_id FROM articles WHERE published >= ?)", (since,)).fetchall()
    best, best_score, best_dup = None, 0.0, 1.0
    toks = tokens(a["title"])
    full = tokens(a["title"] + " " + a["text"][:160])
    for r in rows:
        if vec and r["centroid"]:
            score, thr, dthr = cosine(vec, json.loads(r["centroid"])), ai["similarity"], ai["duplicate_similarity"]
            if len(full & set(json.loads(r["keywords"]))) < 2:
                score = 0.0  # embeddings treat names weakly ("Norris wins" ~ "Piastri penalised"): need 2 shared words
        else:  # lexical fallback: share of this title's words already in the story
            kw = set(json.loads(r["keywords"]))
            inter = len(toks & kw)
            score = inter / len(toks) if toks and inter >= 3 else 0.0
            thr, dthr = ai["lexical_similarity"], ai["lexical_duplicate"]
        if score >= thr and score > best_score:
            best, best_score, best_dup = r["id"], score, dthr
    if best is not None and best_score < ai["similarity"] + 0.05 and vec:  # borderline: log so you can tune the threshold
        t = conn.execute("SELECT title FROM articles WHERE story_id=? LIMIT 1", (best,)).fetchone()
        print(f"  borderline merge ({best_score:.2f}): '{a['title'][:60]}' -> '{(t[0] if t else '?')[:60]}'")
    return best, best_score, best_dup


def _update_story(conn, sid, a, vec):
    row = conn.execute("SELECT centroid, keywords, n_emb FROM stories WHERE id=?", (sid,)).fetchone()
    kw = set(json.loads(row["keywords"]))
    if len(kw) < 40:
        kw |= tokens(a["title"])
    centroid, n = row["centroid"], row["n_emb"]
    if vec:
        if centroid:
            old = json.loads(centroid)
            vec = [(o * n + v) / (n + 1) for o, v in zip(old, vec)]
        centroid, n = json.dumps(vec), n + 1
    conn.execute("UPDATE stories SET centroid=?, keywords=?, n_emb=? WHERE id=?",
                 (centroid, json.dumps(sorted(kw)), n, sid))


# ---------------------------------------------------------------- daily selection

def _bounds(day: date, tz: ZoneInfo):
    start = datetime.combine(day, time.min, tz).astimezone(timezone.utc)
    return start.isoformat(timespec="seconds"), (start + timedelta(days=1)).isoformat(timespec="seconds")


def importance(r, cfg: dict) -> int:
    """Extra rank for big-news words in headlines and for trusted outlets, so a single-source scoop isn't buried."""
    rk = cfg.get("ranking", {})
    titles = (r["titles"] or "").lower()
    boost = 4 if any(w.lower() in titles for w in rk.get("boost_keywords", [])) else 0
    trusted = {x.lower() for x in rk.get("trusted_sources", [])}
    return boost + (3 if trusted & {x.lower() for x in (r["srcnames"] or "").split(",")} else 0)


def build_digest(conn, day: date, llm, cfg: dict, force: bool = False, now: datetime | None = None) -> list[dict]:
    """Everything the user hasn't seen as of `day`. Nothing is dropped:
    top stories get full summaries, overflow + follow-ups go to the compact 'also' tier."""
    g = cfg["general"]
    tz = ZoneInfo(g["timezone"])
    d = day.isoformat()
    if conn.execute("SELECT 1 FROM days WHERE day=?", (d,)).fetchone():
        if not force:
            return load_day(conn, day)
        conn.execute("DELETE FROM entries WHERE day=?", (d,))
    _, end = _bounds(day, tz)
    # "unseen" = ingested since the previous digest was built. Covers outages, late feeds and missed days.
    prev_built = conn.execute("SELECT MAX(built_at) FROM days WHERE day<?", (d,)).fetchone()[0]
    fresh_start, _ = _bounds(day - timedelta(days=g["fresh_days"] - 1), tz)  # only used before any digest exists
    built_at = min((now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), end)

    rows = conn.execute(
        "SELECT story_id, COUNT(*) n_all, SUM(dup=0) n, COUNT(DISTINCT source) srcs, MAX(published) latest, "
        "GROUP_CONCAT(title, ' | ') titles, GROUP_CONCAT(DISTINCT source) srcnames, "
        "MAX(COALESCE(seen_at, published)) latest_seen "
        "FROM articles WHERE published < ? AND COALESCE(seen_at, published) <= ? GROUP BY story_id",
        (end, built_at)).fetchall()
    full_c, follow_c = [], []
    for r in rows:
        if prev_built:
            if r["latest_seen"] <= prev_built:
                continue  # nothing arrived since the last digest
        elif r["latest"] < fresh_start:
            continue  # very first digest: only the last couple of days
        sid = r["story_id"]
        full = conn.execute("SELECT day, headline FROM entries WHERE story_id=? AND day<? AND kind IN ('new','update') "
                            "ORDER BY day DESC LIMIT 1", (sid, d)).fetchone()
        anyl = conn.execute("SELECT article_count FROM entries WHERE story_id=? AND day<? ORDER BY day DESC LIMIT 1",
                            (sid, d)).fetchone()
        if full is None:
            full_c.append((3 * r["srcs"] + r["n_all"] + 2 + importance(r, cfg), "new", r, None))
        elif r["n"] > anyl["article_count"]:
            age = (day - date.fromisoformat(full["day"])).days
            if age > g["resurface_days"]:
                full_c.append((3 * r["srcs"] + r["n_all"] + importance(r, cfg), "update", r, full))
            else:  # seen recently: don't repeat the story, but don't hide genuinely new coverage either
                follow_c.append((r["srcs"], "followup", r, (full, anyl["article_count"])))
        # else: nothing new since last shown -> nothing to say
    full_c.sort(key=lambda c: -c[0])
    follow_c.sort(key=lambda c: -c[0])
    top, rest = full_c[: g["max_items"]], full_c[g["max_items"]:]

    rank = 0
    def put(kind, tier, s, links, conf, n):
        nonlocal rank
        rank += 1
        conn.execute("INSERT INTO entries(day,story_id,rank,kind,article_count,headline,summary,category,confidence,links,tier) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (d, s["sid"], rank, kind, n, s["headline"], s["summary"], s["category"], conf, json.dumps(links), tier))

    def arts_of(sid):
        return [dict(x) for x in conn.execute(
            "SELECT * FROM articles WHERE story_id=? AND published < ? ORDER BY published DESC", (sid, end))]

    def srclinks(arts):
        links, seen = [], set()
        for a in arts:
            if a["source"] not in seen:
                seen.add(a["source"]); links.append({"source": a["source"], "url": a["url"]})
        return links

    def conf(srcs):
        return f"Reported by {srcs} outlets" if srcs >= 2 else "Single source, treat with caution"

    for _, kind, r, _x in top:                       # full read + LLM summary
        arts = arts_of(r["story_id"])
        if cfg["ai"].get("fulltext", True):
            for a in arts[:3]:
                body = feeds.fetch_body(a["url"], cfg["ai"].get("article_chars", 2500))
                if len(body) > 400:
                    a["body"] = body
                    break
        s = llm.summarise(arts, kind)
        put(kind, "top", {**s, "sid": r["story_id"]}, srclinks(arts), conf(r["srcs"]), r["n"])
    for _, kind, r, _x in rest:                      # over the cap: cheap one-liner, still shown
        arts = arts_of(r["story_id"])
        s = extractive(arts, kind)
        put(kind, "also", {**s, "sid": r["story_id"]}, srclinks(arts), conf(r["srcs"]), r["n"])
    for _, _k, r, (full, prev_n) in follow_c:        # new coverage of an already-seen story
        arts = arts_of(r["story_id"])
        novel = [a for a in arts if not a["dup"]]
        fresh = novel[: r["n"] - prev_n] or arts[:1]  # newest-first: only articles that arrived since last shown
        s = {"headline": fresh[0]["title"], "summary": f"Follow-up to: {full['headline']}",
             "category": "Follow-up", "sid": r["story_id"]}
        put("followup", "also", s, srclinks(fresh), conf(len({a["source"] for a in fresh})), r["n"])
    conn.execute("INSERT OR IGNORE INTO days(day, built_at) VALUES(?,?)", (d, built_at))
    conn.commit()
    return load_day(conn, day)


def pending_days(conn, today: date, lookback: int = 3) -> list[str]:
    """Days whose email never went out (send failed, Pi was off): retried on the next run."""
    since = (today - timedelta(days=lookback)).isoformat()
    return [r[0] for r in conn.execute("SELECT day FROM days WHERE emailed=0 AND day>=? ORDER BY day", (since,))]


def load_day(conn, day: date) -> list[dict]:
    rows = conn.execute("SELECT * FROM entries WHERE day=? ORDER BY rank", (day.isoformat(),)).fetchall()
    return [{"kind": r["kind"], "tier": r["tier"] or "top", "headline": r["headline"], "summary": r["summary"],
             "category": r["category"], "confidence": r["confidence"], "links": json.loads(r["links"])}
            for r in rows]


def prune(conn, today: date, cfg: dict):
    g = cfg["general"]
    cutoff = (today - timedelta(days=g["window_days"])).isoformat()
    conn.execute("DELETE FROM entries WHERE day < ?", (cutoff,))
    conn.execute("DELETE FROM days WHERE day < ?", (cutoff,))
    conn.execute("DELETE FROM articles WHERE published < ?", (cutoff,))
    conn.execute("DELETE FROM stories WHERE id NOT IN (SELECT DISTINCT story_id FROM articles)")
    conn.commit()


def archive(conn, today: date, cfg: dict) -> list[dict]:
    """All digests inside the window, newest first (for the web page)."""
    n = cfg["general"]["window_days"]
    days = [today - timedelta(days=i) for i in range(n)]
    out = []
    for dd in days:
        if conn.execute("SELECT 1 FROM days WHERE day=?", (dd.isoformat(),)).fetchone():
            out.append({"day": dd.isoformat(), "items": load_day(conn, dd)})
    return out
