"""SQLite persistence. One file, no server: papers, paragraphs, analyses,
knowledge-graph nodes/edges, chat logs and a cost ledger."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY, path TEXT, title TEXT, n_pages INT,
  page_quality TEXT, added_at REAL, status TEXT DEFAULT 'parsed', progress TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS paragraphs (
  paper_id TEXT, pid TEXT, idx INT, page INT, bbox TEXT, kind TEXT, section TEXT, text TEXT,
  PRIMARY KEY (paper_id, pid)
);
CREATE TABLE IF NOT EXISTS analyses (
  paper_id TEXT, kind TEXT, data TEXT, model TEXT, created_at REAL,
  PRIMARY KEY (paper_id, kind)
);
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY, label TEXT, type TEXT, aliases TEXT, description TEXT, papers TEXT
);
CREATE TABLE IF NOT EXISTS edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT, dst TEXT, rel TEXT, paper_id TEXT,
  evidence TEXT, verified INT, confidence REAL
);
CREATE TABLE IF NOT EXISTS merge_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT, score REAL, status TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, purpose TEXT, model TEXT,
  input_tokens INT, output_tokens INT, cache_read INT, cache_write INT, cost REAL
);
CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, mode TEXT, question TEXT, answer TEXT, meta TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            self._local.c = c
        return c

    # ---- papers
    def upsert_paper(self, parsed) -> None:
        c = self.conn()
        with c:
            c.execute(
                "INSERT INTO papers(id,path,title,n_pages,page_quality,added_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET path=excluded.path, title=excluded.title",
                (parsed.paper_id, parsed.path, parsed.title, parsed.n_pages, json.dumps(parsed.page_quality), time.time()),
            )
            c.execute("DELETE FROM paragraphs WHERE paper_id=?", (parsed.paper_id,))
            c.executemany(
                "INSERT INTO paragraphs VALUES(?,?,?,?,?,?,?,?)",
                [
                    (parsed.paper_id, p.pid, p.idx, p.page, json.dumps(p.bbox), p.kind, p.section, p.text)
                    for p in parsed.paragraphs
                ],
            )

    def papers(self) -> list[dict]:
        rows = self.conn().execute("SELECT * FROM papers ORDER BY title").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["page_quality"] = json.loads(d["page_quality"] or "[]")
            d["analyses"] = [
                x["kind"] for x in self.conn().execute("SELECT kind FROM analyses WHERE paper_id=?", (r["id"],))
            ]
            out.append(d)
        return out

    def paper(self, paper_id: str) -> dict | None:
        r = self.conn().execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["page_quality"] = json.loads(d["page_quality"] or "[]")
        d["analyses"] = [x["kind"] for x in self.conn().execute("SELECT kind FROM analyses WHERE paper_id=?", (paper_id,))]
        return d

    def relink(self, paper_id: str, path: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE papers SET path=? WHERE id=?", (path, paper_id))

    def set_status(self, paper_id: str, status: str, progress: str = "") -> None:
        with self.conn() as c:
            c.execute("UPDATE papers SET status=?, progress=? WHERE id=?", (status, progress, paper_id))

    def paragraphs(self, paper_id: str, kinds: tuple[str, ...] | None = None) -> list[dict]:
        rows = self.conn().execute("SELECT * FROM paragraphs WHERE paper_id=? ORDER BY idx", (paper_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["bbox"] = json.loads(d["bbox"])
            if kinds is None or d["kind"] in kinds:
                out.append(d)
        return out

    def all_paragraphs(self) -> list[dict]:
        rows = self.conn().execute(
            "SELECT p.*, pa.title FROM paragraphs p JOIN papers pa ON pa.id=p.paper_id "
            "WHERE p.kind IN ('body','caption','front') ORDER BY p.paper_id, p.idx"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- analyses
    def save_analysis(self, paper_id: str, kind: str, data, model: str = "") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO analyses VALUES(?,?,?,?,?)",
                (paper_id, kind, json.dumps(data), model, time.time()),
            )

    def analysis(self, paper_id: str, kind: str):
        r = self.conn().execute("SELECT data FROM analyses WHERE paper_id=? AND kind=?", (paper_id, kind)).fetchone()
        return json.loads(r["data"]) if r else None

    # ---- ledger
    def log_cost(self, purpose, model, usage: dict, cost: float) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO ledger(ts,purpose,model,input_tokens,output_tokens,cache_read,cache_write,cost) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    time.time(), purpose, model, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                    usage.get("cache_read_input_tokens", 0), usage.get("cache_creation_input_tokens", 0), cost,
                ),
            )

    def total_cost(self) -> float:
        r = self.conn().execute("SELECT COALESCE(SUM(cost),0) s FROM ledger").fetchone()
        return float(r["s"])

    def cost_summary(self) -> dict:
        rows = self.conn().execute(
            "SELECT purpose, model, COUNT(*) calls, SUM(input_tokens) inp, SUM(output_tokens) outp, "
            "SUM(cache_read) cr, SUM(cache_write) cw, SUM(cost) cost FROM ledger GROUP BY purpose, model"
        ).fetchall()
        return {"total": self.total_cost(), "by_purpose": [dict(r) for r in rows]}

    def log_chat(self, mode, question, answer, meta) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO chats(ts,mode,question,answer,meta) VALUES(?,?,?,?,?)",
                (time.time(), mode, question, answer, json.dumps(meta)),
            )
