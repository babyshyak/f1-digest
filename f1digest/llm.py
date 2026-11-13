"""Local model access (Ollama). Everything degrades gracefully if Ollama is not running."""
import json
import re

import requests

SYSTEM = (
    "You are a precise Formula 1 news editor. Use ONLY facts present in the provided items. "
    "Never speculate, never add numbers or names that are not in the items. "
    'Reply with JSON only: {"headline": "max 12 words", "summary": "1-2 short sentences, max 45 words", '
    '"category": "Race|Driver|Team|Tech|Regulation|Calendar|Other"}'
)


class LLM:
    def __init__(self, cfg: dict):
        self.url = cfg["ollama_url"].rstrip("/")
        self.embed_model = cfg["embed_model"]
        self.chat_model = cfg["chat_model"]
        self._ok = None

    def available(self) -> bool:
        if self._ok is None:
            try:
                requests.get(f"{self.url}/api/tags", timeout=3).raise_for_status()
                self._ok = True
            except Exception:  # noqa: BLE001
                self._ok = False
        return self._ok

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.available() or not texts:
            return None
        try:
            r = requests.post(f"{self.url}/api/embed",
                              json={"model": self.embed_model, "input": texts}, timeout=120)
            r.raise_for_status()
            return r.json()["embeddings"]
        except Exception:  # noqa: BLE001
            return None

    def summarise(self, articles: list[dict], kind: str) -> dict:
        fallback = extractive(articles, kind)
        if not self.available():
            return fallback
        with_body = [a for a in articles if a.get("body")]
        lead = with_body[0] if with_body else None
        items = "\n".join(f"- [{a['source']}] {a['title']}. {a['text'][:200]}" for a in articles[:5] if a is not lead)
        if lead:
            items = f"MAIN ARTICLE [{lead['source']}] {lead['title']}\n{lead['body']}\n\nOTHER COVERAGE:\n{items}"
        prompt = ("This is a NEW DEVELOPMENT in a story already covered; focus on what is new.\n" if kind == "update"
                  else "") + f"Items about one story:\n{items}"
        try:
            r = requests.post(f"{self.url}/api/chat", timeout=180, json={
                "model": self.chat_model, "stream": False, "format": "json",
                "options": {"temperature": 0.1, "num_predict": 160, "num_ctx": 4096},
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            })
            r.raise_for_status()
            data = json.loads(r.json()["message"]["content"])
            head, summ = str(data.get("headline", "")).strip(), str(data.get("summary", "")).strip()
            if len(head) < 5 or len(summ) < 10:
                return fallback
            if not grounded(head + " " + summ, prompt):
                return fallback  # model introduced a name/number not in the sources -> don't publish it
            return {"headline": head[:140], "summary": summ[:400], "category": str(data.get("category", "Other"))[:20]}
        except Exception:  # noqa: BLE001
            return fallback


def extractive(articles: list[dict], kind: str = "new") -> dict:
    """No-LLM fallback: earliest title + first sentences of the richest snippet."""
    ordered = sorted(articles, key=lambda a: a["published"])
    first = ordered[-1] if kind == "update" else ordered[0]  # update -> lead with the newest coverage
    best = max(articles, key=lambda a: len(a["text"]))
    sents = re.split(r"(?<=[.!?])\s+", best["text"])
    summ = " ".join(sents[:2]).strip()[:300] or first["title"]
    return {"headline": first["title"], "summary": summ, "category": "News"}


_ENTITY = re.compile(r"(?<=[a-z,;] )[A-Z][A-Za-z'-]{3,}|\b\d[\d.,:]*\b")


def grounded(generated: str, source: str) -> bool:
    """Every number and mid-sentence capitalised name in the output must appear in the source text."""
    src = source.lower()
    for m in _ENTITY.findall(generated):
        tok = m.lower().rstrip(".,:")
        if tok and tok not in src:
            return False
    return True
