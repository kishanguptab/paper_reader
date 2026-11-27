"""FastAPI backend. Run: uvicorn paperlens.app:app --reload (from backend/)."""
from __future__ import annotations

import logging
import queue
import shutil
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import Agent, Examiner
from .analyze import Analyzer
from .config import settings
from .graph import build_graph
from .ingest import ingest_folder, ingest_file
from .llm import LLM, BudgetExceeded
from .search import Index
from .store import Store

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="PaperLens")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])

store = Store(settings.db_path)
index = Index(store)
_llm: LLM | None = None
_jobs_lock = threading.Lock()
_running: set[str] = set()


def llm() -> LLM:
    global _llm
    if _llm is None:
        try:
            _llm = LLM(store)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
    return _llm


@app.on_event("startup")
def startup():
    # papers left mid-analysis by a previous run are reset
    for p in store.papers():
        if p["status"] in ("queued", "analyzing"):
            store.set_status(p["id"], "parsed" if not p["analyses"] else "analyzed", "")
    if settings.papers_dir.is_dir():
        n = ingest_folder(store, settings.papers_dir)
        if n:
            index.rebuild()


# ------------------------------------------------------------------ library
@app.get("/api/status")
def status():
    return {"papers_dir": str(settings.papers_dir.resolve()), "has_key": bool(settings.anthropic_api_key),
            "models": {"fast": settings.fast_model, "smart": settings.smart_model, "chat": settings.chat_model},
            "budget": settings.budget_usd, "spent": round(store.total_cost(), 4)}


@app.get("/api/papers")
def papers():
    return store.papers()


@app.post("/api/rescan")
def rescan():
    n = ingest_folder(store, settings.papers_dir)
    index.rebuild()
    return {"added_or_updated": n}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF only")
    settings.papers_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.papers_dir / Path(file.filename).name
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    pid = ingest_file(store, dest)
    index.rebuild()
    return {"paper_id": pid}


@app.get("/api/papers/{paper_id}")
def paper(paper_id: str):
    p = store.paper(paper_id)
    if not p:
        raise HTTPException(404)
    return {**p, "paragraphs": store.paragraphs(paper_id),
            "card": store.analysis(paper_id, "card"), "explanations": store.analysis(paper_id, "paragraphs"),
            "threads": store.analysis(paper_id, "threads"), "graph": store.analysis(paper_id, "graph")}


@app.get("/api/papers/{paper_id}/pdf")
def pdf(paper_id: str):
    p = store.paper(paper_id)
    if not p or not Path(p["path"]).is_file():
        raise HTTPException(404)
    return FileResponse(p["path"], media_type="application/pdf")


class AnalyzeReq(BaseModel):
    stages: list[str] = ["card", "graph", "threads", "paragraphs"]
    model: str | None = None  # None = smart model (deep read); "fast" = cheap model


@app.post("/api/papers/{paper_id}/analyze")
def analyze(paper_id: str, req: AnalyzeReq):
    if not store.paper(paper_id):
        raise HTTPException(404)
    llm()  # fail fast if no key
    return {"started": _enqueue(paper_id, tuple(req.stages), req.model)}


@app.post("/api/analyze-all")
def analyze_all(req: AnalyzeReq):
    """Queue every paper missing any of the requested stages. Processed one at a time."""
    llm()
    n = 0
    for p in store.papers():
        todo = tuple(s for s in req.stages if s not in p["analyses"])
        if todo and _enqueue(p["id"], todo, req.model or "fast"):
            n += 1
    return {"queued": n}


# one worker thread: papers are analysed serially (friendly to rate limits + budget)
_queue: "queue.Queue[tuple[str, tuple, str | None]]" = queue.Queue()


def _enqueue(paper_id: str, stages: tuple, model: str | None = None) -> bool:
    with _jobs_lock:
        if paper_id in _running:
            return False
        _running.add(paper_id)
    store.set_status(paper_id, "queued", "waiting in queue")
    _queue.put((paper_id, stages, settings.fast_model if model == "fast" else model))
    return True


def _worker():
    while True:
        paper_id, stages, model = _queue.get()
        try:
            Analyzer(store, llm(), index, model=model).run(paper_id, stages=stages)
        except BudgetExceeded as e:
            store.set_status(paper_id, "error", str(e))
            while not _queue.empty():  # stop the rest of the queue too
                pid, _, _ = _queue.get_nowait()
                store.set_status(pid, "parsed", "")
                _running.discard(pid)
        except Exception:
            pass  # status row carries the error
        finally:
            _running.discard(paper_id)


threading.Thread(target=_worker, daemon=True).start()


# -------------------------------------------------------------------- graph
@app.get("/api/graph")
def graph():
    return build_graph(store)


# --------------------------------------------------------------------- chat
class ChatReq(BaseModel):
    question: str
    history: list[dict] = []
    focus: dict | None = None


@app.post("/api/chat")
def chat(req: ChatReq):
    try:
        return Agent(store, llm(), index).chat(req.question, req.history, req.focus)
    except BudgetExceeded as e:
        raise HTTPException(402, str(e))


@app.get("/api/search")
def search(q: str, k: int = 10):
    return index.search(q, k=k)


# ------------------------------------------------------------------ defense
class QReq(BaseModel):
    paper_ids: list[str]
    asked: list[str] = []
    focus: str = ""
    difficulty: int = 3


class GReq(BaseModel):
    paper_ids: list[str]
    question: dict
    answer: str


@app.post("/api/defense/question")
def defense_q(req: QReq):
    try:
        return Examiner(store, llm()).question(req.paper_ids, req.asked, req.focus, req.difficulty)
    except BudgetExceeded as e:
        raise HTTPException(402, str(e))


@app.post("/api/defense/grade")
def defense_g(req: GReq):
    try:
        return Examiner(store, llm()).grade(req.paper_ids, req.question, req.answer)
    except BudgetExceeded as e:
        raise HTTPException(402, str(e))


@app.get("/api/cost")
def cost():
    return store.cost_summary()


# serve the built frontend if present (single-process mode)
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """Client-side routes (/paper/..., /graph) all serve the React app."""
        f = _dist / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(_dist / "index.html")
