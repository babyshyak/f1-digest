"""Fetch RSS feeds -> normalised article dicts. Uses only the feed's own title/snippet (no page scraping)."""
import hashlib
import html
import re
from datetime import datetime, timezone
from time import mktime
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests

try:
    import trafilatura
except ImportError:  # optional: falls back to feed snippets
    trafilatura = None

TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def clean(text: str, limit: int = 400) -> str:
    text = WS.sub(" ", html.unescape(TAG.sub(" ", text or ""))).strip()
    return text[:limit]


TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|ocid|cid|ito|at_.*|ref|source|xtor|ns_.*)$", re.I)


def norm_url(url: str) -> str:
    """Strip tracking params only: sites that identify articles by ?p=123 / ?id=9 must stay distinct."""
    p = urlsplit(url)
    q = "&".join(sorted(x for x in p.query.split("&") if x and not TRACKING.match(x.split("=")[0])))
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), q, ""))


def article_id(url: str) -> str:
    return hashlib.sha1(norm_url(url).encode()).hexdigest()[:16]


def _published(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(mktime(t), tz=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_feed(feed: dict, timeout: int = 15) -> list[dict]:
    r = requests.get(feed["url"], timeout=timeout, headers={"User-Agent": "f1-digest/1.0"})
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    out = []
    for e in parsed.entries:
        url, title = e.get("link"), clean(e.get("title", ""), 200)
        if not url or not title:
            continue
        out.append({
            "id": article_id(url),
            "url": url,
            "title": title,
            "source": feed["name"],
            "published": _published(e).isoformat(timespec="seconds"),
            "text": clean(e.get("summary") or e.get("description") or ""),
            "broad": bool(feed.get("broad")),
        })
    return out


def fetch_all(feeds: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """Returns (articles, report). A dead feed never stops the run."""
    arts, report = [], []
    for f in feeds:
        try:
            got = fetch_feed(f)
            arts += got
            report.append((f["name"], f"ok ({len(got)} items)"))
        except Exception as ex:  # noqa: BLE001
            report.append((f["name"], f"FAILED: {type(ex).__name__}: {str(ex)[:80]}"))
    return arts, report


def fetch_body(url: str, limit: int = 2500, timeout: int = 15) -> str:
    """Main article text from the page (nav/ads stripped). Empty string on any failure or paywall."""
    if trafilatura is None:
        return ""
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (compatible; f1-digest/1.0)"})
        r.raise_for_status()
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False) or ""
        return clean(text, limit)
    except Exception:  # noqa: BLE001
        return ""


def filter_articles(arts: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """Drop noise by title (exclude) and off-topic items from broad aggregator feeds (require_any)."""
    f = cfg.get("filters", {})
    ex = [w.lower() for w in f.get("exclude", [])]
    req = [w.lower() for w in f.get("require_any", [])]
    kept, dropped = [], []
    for a in arts:
        title, blob = a["title"].lower(), (a["title"] + " " + a["text"]).lower()
        if any(w in title for w in ex) or (a.get("broad") and req and not any(w in blob for w in req)):
            dropped.append(a)
        else:
            kept.append(a)
    return kept, dropped
