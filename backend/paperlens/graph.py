"""Library-wide knowledge graph, rebuilt deterministically from per-paper extractions.

Entity resolution is the failure mode here: "GPTQ" vs "GPTQ quantization",
"StereoSet" vs "StereoSet benchmark", "KD" vs "knowledge distillation". We merge
on a normalised key + aliases automatically, and send near-misses (similar
but not certain) to a review queue rather than silently merging them, because
a wrong merge creates false cross-paper connections.
"""
from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher

GENERIC_SUFFIX = re.compile(r"\b(benchmark|dataset|method|algorithm|metric|model|framework|technique|approach|score)s?$")
TYPE_COLORS = {
    "paper": "#2563eb", "method": "#7c3aed", "dataset": "#059669", "benchmark": "#059669", "metric": "#d97706",
    "model": "#db2777", "concept": "#0891b2", "task": "#65a30d", "bias-type": "#dc2626", "finding": "#4f46e5",
    "limitation": "#b91c1c",
}


def ekey(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"\(([^)]*)\)", " ", s)  # drop parenthetical acronyms; they're added as aliases
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = GENERIC_SUFFIX.sub("", s).strip()
    if len(s) > 4 and s.endswith("s") and not s.endswith("ss"):
        s = s[:-1]
    return s or name.lower()


def _aliases(e: dict) -> set[str]:
    out = {ekey(e["name"])}
    for a in e.get("aliases", []) or []:
        out.add(ekey(a))
    for m in re.findall(r"\(([^)]+)\)", e["name"]):
        out.add(ekey(m))
    return {a for a in out if a}


def title_match(a: str, b: str) -> float:
    na, nb = re.sub(r"[^a-z0-9 ]", "", a.lower()), re.sub(r"[^a-z0-9 ]", "", b.lower())
    if na and (na in nb or nb in na):
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def build_graph(store, include_review: bool = True) -> dict:
    papers = {p["id"]: p for p in store.papers()}
    canon: dict[str, str] = {}  # alias key -> node id
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    review: list[dict] = []

    for pid, p in papers.items():
        nodes[f"paper:{pid}"] = {"id": f"paper:{pid}", "label": p["title"], "type": "paper", "papers": [pid], "description": ""}

    extractions = {pid: store.analysis(pid, "graph") for pid in papers}
    for pid, g in extractions.items():
        if not g:
            continue
        local: dict[str, str] = {}
        for e in g.get("entities", []):
            keys = _aliases(e)
            nid = next((canon[k] for k in keys if k in canon), None)
            if nid is None:
                nid = "e:" + ekey(e["name"]).replace(" ", "_")
                nodes[nid] = {"id": nid, "label": e["name"], "type": e.get("type", "concept"), "papers": [],
                              "description": e.get("description", ""), "aliases": sorted(keys)}
            n = nodes[nid]
            if pid not in n["papers"]:
                n["papers"].append(pid)
            n["aliases"] = sorted(set(n.get("aliases", [])) | keys)
            for k in keys:
                canon.setdefault(k, nid)
            local[e["name"].lower()] = nid
            for a in e.get("aliases", []) or []:
                local[a.lower()] = nid
            edges.append({"src": f"paper:{pid}", "dst": nid, "rel": "mentions", "paper_id": pid,
                          "grounded": e.get("grounded", False), "evidence": e.get("evidence", [])})

        for r in g.get("relations", []):
            s = local.get(r["src"].lower()) or canon.get(ekey(r["src"]))
            d = local.get(r["dst"].lower()) or canon.get(ekey(r["dst"]))
            if s and d and s != d:
                edges.append({"src": s, "dst": d, "rel": r["rel"], "paper_id": pid,
                              "grounded": r.get("grounded", False), "evidence": r.get("evidence", [])})

        for link in g.get("paper_links", []):
            best = max(((title_match(link["other_title"], q["title"]), qid) for qid, q in papers.items() if qid != pid), default=(0, None))
            if best[0] >= 0.85:
                edges.append({"src": f"paper:{pid}", "dst": f"paper:{best[1]}", "rel": link["rel"], "paper_id": pid,
                              "explanation": link.get("explanation", ""), "grounded": link.get("grounded", False),
                              "evidence": link.get("evidence", [])})

    # near-duplicate entities -> review queue (never auto-merged)
    if include_review:
        ents = [n for n in nodes.values() if n["type"] != "paper"]
        for i, a in enumerate(ents):
            ka = ekey(a["label"])
            for b in ents[i + 1 :]:
                if a["type"] != b["type"]:
                    continue
                kb = ekey(b["label"])
                sim = SequenceMatcher(None, ka, kb).ratio()
                contained = (ka in kb.split() or kb in ka.split()) and min(len(ka), len(kb)) >= 3
                if 0.82 <= sim < 1.0 or contained:
                    review.append({"a": a["id"], "b": b["id"], "a_label": a["label"], "b_label": b["label"], "score": round(sim, 2)})

    # shared-entity paper links (computed, not model-claimed)
    by_entity = defaultdict(set)
    for n in nodes.values():
        if n["type"] != "paper":
            for p in n["papers"]:
                by_entity[n["id"]].add(p)
    shared = defaultdict(list)
    for nid, ps in by_entity.items():
        ps = sorted(ps)
        for i, a in enumerate(ps):
            for b in ps[i + 1 :]:
                shared[(a, b)].append(nodes[nid]["label"])

    for n in nodes.values():
        n["color"] = TYPE_COLORS.get(n["type"], "#64748b")
        n["degree"] = 0
    for e in edges:
        for k in ("src", "dst"):
            if e[k] in nodes:
                nodes[e[k]]["degree"] += 1

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "review": review[:200],
        "shared": [{"a": a, "b": b, "entities": v} for (a, b), v in sorted(shared.items(), key=lambda kv: -len(kv[1]))][:300],
    }


def neighbors(graph: dict, query: str, limit: int = 30) -> dict:
    """Used by the chat agent: find nodes matching `query` and their edges."""
    q = ekey(query)
    hits = [n for n in graph["nodes"] if q and (q in ekey(n["label"]) or q in (n.get("aliases") or []))]
    ids = {n["id"] for n in hits}
    es = [e for e in graph["edges"] if e["src"] in ids or e["dst"] in ids][:limit]
    label = {n["id"]: n["label"] for n in graph["nodes"]}
    return {
        "matches": [{"id": n["id"], "label": n["label"], "type": n["type"], "papers": n["papers"], "description": n.get("description", "")} for n in hits[:10]],
        "edges": [{"src": label.get(e["src"], e["src"]), "rel": e["rel"], "dst": label.get(e["dst"], e["dst"]), "paper_id": e["paper_id"], "grounded": e.get("grounded")} for e in es],
    }
