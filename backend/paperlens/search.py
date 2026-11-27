"""BM25 search over every paragraph of every paper (no external services).

Retrieval is deliberately lexical + transparent: you can see why a paragraph
matched. An abstention threshold lets the agent say "not in the papers"
instead of stretching a weak match into an answer.
"""
from __future__ import annotations

import re
import threading

from rank_bm25 import BM25Okapi

STOP = set(
    "a an the of and or to in on for with by is are was were be been this that these those it its as at from we our "
    "their they which what how why does do did can could would should into than then also such not no".split()
)
ABSTAIN_THRESHOLD = 0.5  # IDF-weighted share of query terms found in the best paragraph
TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]*")


def tokenize(s: str) -> list[str]:
    return [t for t in TOKEN.findall(s.lower()) if t not in STOP and len(t) > 1]


class Index:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self.rebuild()

    def rebuild(self):
        with self._lock:
            self.docs = self.store.all_paragraphs()
            toks = [tokenize(f"{d['section']} {d['text']}") for d in self.docs]
            self.bm25 = BM25Okapi(toks) if toks else None

    def search(self, query: str, k: int = 8, paper_ids: list[str] | None = None) -> list[dict]:
        if not self.bm25:
            return []
        q = tokenize(query)
        if not q:
            return []
        scores = self.bm25.get_scores(q)
        idx = sorted(range(len(scores)), key=lambda i: -scores[i])
        out = []
        top = scores[idx[0]] if idx else 0
        for i in idx:
            if scores[i] <= 0:
                break
            d = self.docs[i]
            if paper_ids and d["paper_id"] not in paper_ids:
                continue
            qset = set(q)
            dset = set(tokenize(f"{d['section']} {d['text']}"))
            idf = self.bm25.idf
            w = {t: max(idf.get(t, max(idf.values())), 0.05) for t in qset}  # unseen term = rarest
            overlap = sum(w[t] for t in qset & dset) / sum(w.values())
            out.append(
                {
                    "paper_id": d["paper_id"], "title": d["title"], "pid": d["pid"], "page": d["page"],
                    "section": d["section"], "text": d["text"], "score": round(float(scores[i]), 3),
                    "rel_score": round(float(scores[i] / top), 3) if top else 0, "term_overlap": round(overlap, 2),
                }
            )
            if len(out) >= k:
                break
        return out
