"""Evaluation suite. Free evals run without an API key; --llm adds paid ones.

1. parser     : section detection + page text quality across the library
2. verifier   : can the evidence checker tell real quotes from fabricated ones?
                (synthetic: true substrings vs. word-swapped / cross-paragraph / invented quotes)
3. retrieval  : for questions with a known source paper, is it in the top-5? For
                trap questions the papers can't answer, does the abstention signal fire?
4. grounding  : share of card/graph claims whose evidence quotes verified (from stored analyses)
5. chat (--llm): answer questions end to end; measure citation validity and
                correct abstention on trap questions
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from .search import Index, ABSTAIN_THRESHOLD
from .verify import quote_score

ROOT = Path(__file__).resolve().parents[2]


def _pct(a, b):
    return round(100 * a / b, 1) if b else 0.0


def eval_parser(store) -> dict:
    ps = store.papers()
    found_abs = found_intro = good_pages = pages = 0
    for p in ps:
        secs = {x["section"].lower() for x in store.paragraphs(p["id"], kinds=("heading",))}
        found_abs += any(s.startswith("abstract") for s in secs)
        found_intro += any("introduction" in s for s in secs)
        pages += len(p["page_quality"])
        good_pages += sum(q >= 0.8 for q in p["page_quality"])
    return {"papers": len(ps), "abstract_detected_%": _pct(found_abs, len(ps)),
            "introduction_detected_%": _pct(found_intro, len(ps)), "pages_good_quality_%": _pct(good_pages, pages)}


def eval_verifier(store, n: int = 400, seed: int = 7) -> dict:
    rng = random.Random(seed)
    paras = [p for p in store.all_paragraphs() if len(p["text"].split()) > 40]
    if len(paras) < 10:
        return {"skipped": "need more paragraphs"}
    tp = fp = tn = fn = 0
    kinds = {"exact": [0, 0], "pdf_noise": [0, 0], "swapped": [0, 0], "cross_paragraph": [0, 0], "invented": [0, 0]}
    for _ in range(n):
        p = rng.choice(paras)
        w = p["text"].split()
        L = rng.randint(8, 25)
        s = rng.randint(0, len(w) - L)
        true_q = " ".join(w[s : s + L])
        kind = rng.choice(list(kinds))
        if kind == "exact":
            q, label = true_q, True
        elif kind == "pdf_noise":  # model "fixes" a ligature / hyphenation: one char off in a long word
            ww = w[s : s + L]
            longs = [i for i, x in enumerate(ww) if len(x) > 6 and x.isalpha()]
            if longs:
                i = rng.choice(longs)
                ww[i] = ww[i][:3] + ww[i][4:]
            q, label = " ".join(ww), True
        elif kind == "swapped":  # change the meaning: swap/negate key words
            ww = w[s : s + L]
            i = rng.randrange(len(ww))  # a single word changed is enough to flip a claim
            new = rng.choice(["not", "never", "increases", "decreases", "always", "worse", "better", "50%"])
            label = ww[i].lower().strip(".,;:") == new
            ww[i] = new
            q = " ".join(ww)
        elif kind == "cross_paragraph":
            o = rng.choice(paras)
            ow = o["text"].split()
            os_ = rng.randint(0, max(0, len(ow) - L))
            q, label = " ".join(ow[os_ : os_ + L]), o["text"] == p["text"]
        else:
            q, label = "the proposed method consistently reduces bias across all evaluated demographic groups without loss of accuracy", False
        pred = quote_score(q, p["text"]) >= 0.85
        kinds[kind][0] += pred == label
        kinds[kind][1] += 1
        tp += pred and label
        fp += pred and not label
        tn += (not pred) and (not label)
        fn += (not pred) and label
    return {"n": n, "precision": round(tp / max(1, tp + fp), 3), "recall": round(tp / max(1, tp + fn), 3),
            "accuracy_by_kind": {k: _pct(v[0], v[1]) for k, v in kinds.items()}}


def _load_questions(path) -> list[dict]:
    path = Path(path or ROOT / "eval" / "questions.jsonl")
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip() and not l.startswith("//")]


def eval_retrieval(store, questions) -> dict:
    idx = Index(store)
    titles = {p["id"]: p["title"].lower() for p in store.papers()}
    ans = [q for q in questions if q["answerable"]]
    trap = [q for q in questions if not q["answerable"]]
    hit5 = covered = 0
    misses = []
    for q in ans:
        target = [pid for pid, t in titles.items() if q["paper"].lower() in t]
        if not target:
            continue
        covered += 1
        hits = idx.search(q["question"], k=5)
        if any(h["paper_id"] in target for h in hits):
            hit5 += 1
        else:
            misses.append(q["question"])
    abstain = 0
    for q in trap:
        hits = idx.search(q["question"], k=5)
        if not hits or max(h["term_overlap"] for h in hits) < ABSTAIN_THRESHOLD:
            abstain += 1
    return {"answerable_questions": covered, "paper_recall@5_%": _pct(hit5, covered),
            "trap_questions": len(trap), "retrieval_abstain_signal_%": _pct(abstain, len(trap)), "misses": misses[:10]}


def eval_grounding(store) -> dict:
    tot = {"claims": 0, "grounded": 0, "quotes": 0, "quotes_ok": 0}
    n = 0
    for p in store.papers():
        for kind in ("card", "graph"):
            a = store.analysis(p["id"], kind)
            if a and a.get("verification"):
                n += kind == "card"
                for k in tot:
                    tot[k] += a["verification"].get(k, 0)
    return {"papers_with_cards": n, "claims": tot["claims"], "claims_grounded_%": _pct(tot["grounded"], tot["claims"]),
            "quotes_verified_%": _pct(tot["quotes_ok"], tot["quotes"])}


ABSTAIN_RE = re.compile(r"don't state|do not state|not (?:stated|reported|mentioned|covered)|doesn't (?:say|report)|no (?:paper|information)|couldn't find|could not find|not in (?:the|your)", re.I)


def eval_chat(store, questions, sample: bool = True) -> dict:
    """End-to-end agent eval. sample=True keeps cost down (~$2): 6 answerable
    questions, every hard in-domain trap, and 2 easy off-topic traps."""
    from .agent import Agent
    from .llm import LLM

    if sample:
        ans = [q for q in questions if q["answerable"]][:6]
        hard = [q for q in questions if not q["answerable"] and q.get("hard")]
        easy = [q for q in questions if not q["answerable"] and not q.get("hard")][:2]
        questions = ans + hard + easy
    agent = Agent(store, LLM(store), Index(store))
    rows = []
    for q in questions:
        r = agent.chat(q["question"])
        abstained = bool(ABSTAIN_RE.search(r["answer"][:600]))
        rows.append({"q": q["question"], "answerable": q["answerable"], "hard": q.get("hard", False), "abstained": abstained,
                     "answer_head": r["answer"][:400],
                     "citations": r["check"]["citations"], "invalid": len(r["check"]["invalid_citations"]),
                     "uncited": r["check"]["uncited_sentences"], "sentences": r["check"]["sentences"]})
    ans = [r for r in rows if r["answerable"]]
    trap = [r for r in rows if not r["answerable"]]
    return {
        "n": len(rows),
        "answered_with_citations_%": _pct(sum(r["citations"] > 0 and not r["abstained"] for r in ans), len(ans)),
        "invalid_citation_rate_%": _pct(sum(r["invalid"] for r in rows), sum(r["citations"] for r in rows)),
        "uncited_sentence_rate_%": _pct(sum(r["uncited"] for r in rows), sum(r["sentences"] for r in rows)),
        "trap_correctly_abstained_%": _pct(sum(r["abstained"] for r in trap), len(trap)),
        "hard_trap_correctly_abstained_%": _pct(sum(r["abstained"] for r in trap if r["hard"]), sum(r["hard"] for r in trap)),
        "false_abstain_on_answerable_%": _pct(sum(r["abstained"] for r in ans), len(ans)),
        "rows": rows,
    }


def run_evals(store, use_llm=False, path=None) -> dict:
    qs = _load_questions(path)
    out = {"parser": eval_parser(store), "verifier": eval_verifier(store), "retrieval": eval_retrieval(store, qs),
           "grounding": eval_grounding(store)}
    if use_llm:
        out["chat"] = eval_chat(store, qs)
    print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk != "rows"} if isinstance(v, dict) else v) for k, v in out.items()}, indent=2))
    res = ROOT / "eval" / "results.json"
    res.write_text(json.dumps(out, indent=2))
    print(f"\nfull results -> {res}")
    return out
