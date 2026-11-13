import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories(
  id INTEGER PRIMARY KEY AUTOINCREMENT, centroid TEXT, keywords TEXT, n_emb INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS articles(
  id TEXT PRIMARY KEY, story_id INTEGER, url TEXT, title TEXT, source TEXT, published TEXT, text TEXT, dup INTEGER DEFAULT 0, seen_at TEXT);
CREATE INDEX IF NOT EXISTS ix_art_story ON articles(story_id);
CREATE INDEX IF NOT EXISTS ix_art_pub ON articles(published);
CREATE TABLE IF NOT EXISTS entries(
  day TEXT, story_id INTEGER, rank INTEGER, kind TEXT, article_count INTEGER,
  headline TEXT, summary TEXT, category TEXT, confidence TEXT, links TEXT, tier TEXT DEFAULT 'top',
  PRIMARY KEY(day, story_id));
CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, emailed INTEGER DEFAULT 0, built_at TEXT);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for stmt in ("ALTER TABLE articles ADD COLUMN dup INTEGER DEFAULT 0",
                 "ALTER TABLE articles ADD COLUMN seen_at TEXT",
                 "ALTER TABLE days ADD COLUMN built_at TEXT"):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    try:  # migrate databases created before tiers existed
        conn.execute("ALTER TABLE entries ADD COLUMN tier TEXT DEFAULT 'top'")
    except sqlite3.OperationalError:
        pass
    return conn


def backup(conn: sqlite3.Connection, dest_dir: str | Path, today: str, keep: int = 7) -> Path:
    """Consistent snapshot via SQLite's online backup API; keeps the newest `keep` files."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"f1-{today}.db"
    out = sqlite3.connect(target)
    with out:
        conn.backup(out)
    out.close()
    for old in sorted(dest.glob("f1-*.db"))[:-keep]:
        old.unlink()
    return target
