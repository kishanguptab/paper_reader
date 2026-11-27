"""CLI.

  python -m paperlens ingest                 parse every PDF in PAPERLENS_PAPERS_DIR
  python -m paperlens list
  python -m paperlens analyze <id|all> [--stages card,graph] [--max-usd 5]
  python -m paperlens ask "question"
  python -m paperlens cost
  python -m paperlens eval [--llm]           run the eval suite (see eval/README.md)
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import settings
from .store import Store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="paperlens")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    sub.add_parser("list")
    a = sub.add_parser("analyze")
    a.add_argument("target")
    a.add_argument("--stages", default="card,graph,threads,paragraphs")
    a.add_argument("--max-usd", type=float, default=None, help="stop once total spend reaches this")
    a.add_argument("--quick", action="store_true", help="use the fast model for card/graph (library-wide skim)")
    a.add_argument("--workers", type=int, default=1, help="papers analysed in parallel")
    q = sub.add_parser("ask")
    q.add_argument("question")
    sub.add_parser("cost")
    e = sub.add_parser("eval")
    e.add_argument("--llm", action="store_true", help="also run LLM-graded evals (costs credits)")
    e.add_argument("--file", default=None)
    args = ap.parse_args(argv)

    store = Store(settings.db_path)

    if args.cmd == "ingest":
        from .ingest import ingest_folder

        n = ingest_folder(store, settings.papers_dir)
        print(f"ingested {n} new paper(s) from {settings.papers_dir.resolve()}; library has {len(store.papers())}")
    elif args.cmd == "list":
        for p in store.papers():
            print(f"{p['id']}  {p['n_pages']:>3}p  {','.join(p['analyses']) or '-':32}  {p['title'][:80]}")
    elif args.cmd == "analyze":
        from .analyze import Analyzer
        from .llm import LLM, BudgetExceeded
        from .search import Index

        if args.max_usd is not None:
            settings.budget_usd = args.max_usd
        from concurrent.futures import ThreadPoolExecutor

        llm = LLM(store)
        an = Analyzer(store, llm, Index(store), model=settings.fast_model if args.quick else None)
        ids = [p["id"] for p in store.papers()] if args.target == "all" else [args.target]
        stages = tuple(s.strip() for s in args.stages.split(",") if s.strip())
        jobs = []
        for pid in ids:
            have = set(store.paper(pid)["analyses"])
            todo = tuple(s for s in stages if s not in have or (s == "paragraphs" and args.target != "all"))
            if todo:
                jobs.append((pid, todo))
        print(f"{len(jobs)} paper(s) to analyse with {an.smart} :: {','.join(stages)}", flush=True)
        stop = {"budget": False}

        def one(i_job):
            i, (pid, todo) = i_job
            if stop["budget"]:
                return
            title = store.paper(pid)["title"]
            try:
                an.run(pid, stages=todo, on_progress=(lambda m: print("   ", m, flush=True)) if args.workers == 1 else None)
                print(f"[{i}/{len(jobs)}] ok  {title[:70]}  (total ${store.total_cost():.3f})", flush=True)
            except BudgetExceeded as e:
                stop["budget"] = True
                print(str(e), flush=True)
            except Exception as e:
                print(f"[{i}/{len(jobs)}] FAILED {title[:60]}: {str(e)[:160]}", flush=True)

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            list(ex.map(one, enumerate(jobs, 1)))
        print(f"done. total spent ${store.total_cost():.3f}")
    elif args.cmd == "ask":
        from .agent import Agent
        from .llm import LLM
        from .search import Index

        r = Agent(store, LLM(store), Index(store)).chat(args.question)
        print(r["answer"])
        print("\n--", json.dumps(r["check"]))
    elif args.cmd == "cost":
        print(json.dumps(store.cost_summary(), indent=2))
    elif args.cmd == "eval":
        from .evals import run_evals

        run_evals(store, use_llm=args.llm, path=args.file)


if __name__ == "__main__":
    sys.exit(main())
