"""The paper-understanding pipeline.

Stage 1  card        (smart model)  aim, RQs, hypotheses, method, experiments,
                                    results + how they're reported, limitations
Stage 2  paragraphs  (fast model)   plain-language explanation of every paragraph,
                                    its role, terms, and links to the card
Stage 3  threads     (smart model)  idea chains: hypothesis -> method -> experiment
                                    -> result -> limitation, with a verdict
Stage 4  graph       (smart model)  entities + relations, and links to the other
                                    papers in the library

Every claim carries {pid, quote} evidence and is verified after generation.
All four stages share one cached copy of the paper text.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from .config import settings
from .llm import LLM, paper_system
from .verify import verify_tree

log = logging.getLogger("paperlens")

MAX_CHARS = 300_000  # ~75k tokens; longer papers get appendices trimmed

EVIDENCE = {
    "type": "array",
    "description": "1-2 verbatim quotes (6-25 words) copied exactly from the cited paragraph.",
    "items": {
        "type": "object",
        "properties": {"pid": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["pid", "quote"],
    },
}


def item(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {**props, "evidence": EVIDENCE}, "required": required + ["evidence"]}


S = {"type": "string"}
SL = {"type": "array", "items": {"type": "string"}}

CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "tldr": {"type": "string", "description": "2-3 sentences a smart non-specialist understands."},
        "paper_type": {"type": "string", "enum": ["empirical", "method", "theory", "benchmark/dataset", "survey", "position", "other"]},
        "field": S,
        "aim": item({"text": S}, ["text"]),
        "problem_and_motivation": item({"text": S}, ["text"]),
        "research_questions": {"type": "array", "items": item({"id": {"type": "string", "description": "RQ1, RQ2..."}, "text": S}, ["id", "text"])},
        "hypotheses": {
            "type": "array",
            "description": "What the authors expect to be true. If not stated as a hypothesis, infer it from the claims they test and set explicit=false.",
            "items": item({"id": {"type": "string", "description": "H1, H2..."}, "text": S, "explicit": {"type": "boolean"}}, ["id", "text", "explicit"]),
        },
        "contributions": {"type": "array", "items": item({"text": S}, ["text"])},
        "methodology": {
            "type": "object",
            "properties": {
                "overview": S,
                "components": {"type": "array", "items": item({"id": {"type": "string", "description": "M1, M2..."}, "name": S, "description": S}, ["id", "name", "description"])},
            },
            "required": ["overview", "components"],
        },
        "experiments": {
            "type": "array",
            "items": item(
                {
                    "id": {"type": "string", "description": "E1, E2..."}, "name": S,
                    "purpose": {"type": "string", "description": "Which RQ/H it tests and how."},
                    "tests": {**SL, "description": "ids of RQs/Hs it tests"},
                    "datasets": SL, "models": SL, "baselines": SL, "metrics": SL,
                    "setup": {"type": "string", "description": "Key settings: splits, seeds, hyperparameters, compute, repeats."},
                },
                ["id", "name", "purpose", "tests", "datasets", "models", "baselines", "metrics", "setup"],
            ),
        },
        "results": {
            "type": "array",
            "items": item(
                {
                    "id": {"type": "string", "description": "R1, R2..."}, "claim": S,
                    "from_experiment": S,
                    "supports": {**SL, "description": "H/RQ ids this result bears on"},
                    "how_reported": {"type": "string", "description": "Where/how: e.g. 'Table 3, mean of 3 seeds, no CI', 'Figure 2 bar chart', 'text only'."},
                    "key_numbers": S,
                    "statistical_rigor": {"type": "string", "enum": ["significance tested", "variance/CI reported", "single run", "unclear"]},
                    "strength": {"type": "string", "enum": ["strong", "moderate", "weak"]},
                },
                ["id", "claim", "supports", "how_reported", "key_numbers", "statistical_rigor", "strength"],
            ),
        },
        "limitations_stated": {"type": "array", "items": item({"text": S}, ["text"])},
        "limitations_inferred": {
            "type": "array",
            "description": "Gaps a careful reviewer would raise that the authors do not state. Evidence = the paragraph showing the gap.",
            "items": item({"text": S, "reasoning": S}, ["text", "reasoning"]),
        },
        "future_work": {"type": "array", "items": item({"text": S}, ["text"])},
        "key_terms": {"type": "array", "items": item({"term": S, "definition": S}, ["term", "definition"])},
    },
    "required": [
        "tldr", "paper_type", "field", "aim", "problem_and_motivation", "research_questions", "hypotheses",
        "contributions", "methodology", "experiments", "results", "limitations_stated", "limitations_inferred",
        "future_work", "key_terms",
    ],
}

CARD_PARTS = [
    (("tldr", "paper_type", "field", "aim", "problem_and_motivation", "research_questions", "hypotheses", "contributions", "key_terms"),
     "Part 1 of the paper card: what the paper sets out to do. Give every research question and hypothesis "
     "(infer hypotheses from what is tested when not stated, marking explicit=false), the contributions, and 6-12 key terms."),
    (("methodology", "experiments"),
     "Part 2: how the work was done. Break the methodology into 2-6 components (M1, M2...). List every experiment "
     "(E1, E2...) with the RQs/hypotheses it tests, datasets, models, baselines, metrics and the concrete setup: "
     "splits, seeds, hyperparameters, compute, number of runs."),
    (("results", "limitations_stated", "limitations_inferred", "future_work"),
     "Part 3: what they found and what it is worth. List the 4-10 headline results (R1, R2...), which hypotheses it bears on, "
     "and exactly how it is reported (which table/figure, aggregation, seeds, error bars, significance tests, or none). "
     "Rate evidence strength honestly. Then the limitations the authors state, and separately the ones a skeptical "
     "committee member would raise: sample size, missing baselines, metric validity, generalisation, confounds, "
     "compute, reproducibility."),
]

PARA_SCHEMA = {
    "type": "object",
    "properties": {
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pid": S,
                    "explanation": {"type": "string", "description": "Plain-language explanation, 2-5 sentences. Unpack notation and jargon."},
                    "role": {"type": "string", "enum": ["motivation", "background", "related-work", "definition", "claim", "hypothesis", "method", "experimental-setup", "result", "analysis", "limitation", "future-work", "figure/table", "other"]},
                    "why_it_matters": S,
                    "key_terms": SL,
                    "depends_on": {**SL, "description": "earlier pids needed to understand this one"},
                    "links": {**SL, "description": "card ids it relates to (H1, E2, R3, M1...)"},
                    "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["pid", "explanation", "role", "why_it_matters", "key_terms", "depends_on", "links", "difficulty"],
            },
        }
    },
    "required": ["paragraphs"],
}

THREAD_SCHEMA = {
    "type": "object",
    "properties": {
        "threads": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": S, "title": S,
                    "kind": {"type": "string", "enum": ["hypothesis-to-evidence", "concept", "method-pipeline", "argument", "limitation"]},
                    "summary": S,
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"pid": S, "stage": {"type": "string", "enum": ["question", "hypothesis", "definition", "method", "experiment", "result", "interpretation", "limitation"]}, "note": S, "card_ref": S},
                            "required": ["pid", "stage", "note"],
                        },
                    },
                    "verdict": {"type": "string", "description": "Is the chain complete? Is the claim actually tested? What is missing?"},
                    "gaps": SL,
                },
                "required": ["id", "title", "kind", "summary", "steps", "verdict", "gaps"],
            },
        }
    },
    "required": ["threads"],
}

GRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": item(
                {
                    "name": {"type": "string", "description": "Canonical name, e.g. 'GPTQ', 'StereoSet', 'equalized odds'"},
                    "type": {"type": "string", "enum": ["concept", "method", "dataset", "benchmark", "metric", "model", "task", "bias-type", "finding", "limitation"]},
                    "aliases": SL, "description": S,
                },
                ["name", "type", "aliases", "description"],
            ),
        },
        "relations": {
            "type": "array",
            "items": item(
                {
                    "src": S, "dst": S,
                    "rel": {"type": "string", "enum": ["proposes", "uses", "evaluates-on", "measures", "compares-with", "improves-on", "extends", "is-a", "part-of", "causes", "mitigates", "correlates-with", "does-not-correlate-with", "finds", "limited-by"]},
                },
                ["src", "dst", "rel"],
            ),
        },
        "paper_links": {
            "type": "array",
            "description": "Links from THIS paper to other papers in the library list (only those actually related).",
            "items": item(
                {
                    "other_title": S,
                    "rel": {"type": "string", "enum": ["cites", "extends", "contradicts", "supports", "uses-dataset-of", "uses-method-of", "compares-with", "addresses-limitation-of", "same-problem"]},
                    "explanation": S,
                },
                ["other_title", "rel", "explanation"],
            ),
        },
    },
    "required": ["entities", "relations", "paper_links"],
}

ROLE = (
    "You are PaperLens, a meticulous research mentor helping a PhD student understand a paper "
    "deeply enough to defend it before a committee. Be precise, concrete and honest. "
    "Never invent content: if the paper does not state something, say so. Distinguish what the "
    "authors claim from what their evidence shows."
)


def numbered_text(paragraphs: list[dict]) -> str:
    lines, total = [], 0
    for p in paragraphs:
        if p["kind"] in ("reference", "figure_text"):
            continue
        tag = f"[{p['pid']}] ({p['section']}, p.{p['page']}{', ' + p['kind'] if p['kind'] in ('heading', 'caption') else ''}) "
        line = tag + p["text"]
        total += len(line)
        if total > MAX_CHARS:
            lines.append("[... remainder truncated for length ...]")
            break
        lines.append(line)
    return "\n".join(lines)


class Analyzer:
    """`model` overrides the synthesis model: library-wide quick cards use the fast
    (cheap) model; a deep read of one paper uses the smart model."""

    def __init__(self, store, llm: LLM, index=None, model: str | None = None):
        self.store, self.llm, self.index = store, llm, index
        self.smart = model or settings.smart_model

    def _ctx(self, paper_id: str):
        paper = self.store.paper(paper_id)
        paras = self.store.paragraphs(paper_id)
        text = numbered_text(paras)
        lookup = {p["pid"]: p["text"] for p in paras}
        return paper, paras, paper_system(paper["title"], text, ROLE), lookup

    # ---------------------------------------------------------------- stage 1
    def card(self, paper_id: str) -> dict:
        """Built in three calls over the same cached paper (one schema is too large
        for constrained decoding, and smaller outputs are more reliable)."""
        paper, paras, system, lookup = self._ctx(paper_id)
        rules = ("Be concise: one or two sentences per field. Each item gets 1-2 evidence quotes of 6-25 words, copied "
                 "exactly from the cited paragraph, with its pid. Never paraphrase inside a quote.")
        data: dict = {}
        for i, (keys, task) in enumerate(CARD_PARTS):
            sub = {"type": "object", "properties": {k: CARD_SCHEMA["properties"][k] for k in keys}, "required": list(keys)}
            context = f"Already extracted (keep ids consistent): {json.dumps(_card_index(data))}\n\n" if data else ""
            part = self.llm.structured(
                purpose=f"card-{i + 1}", model=self.smart, system=system, schema=sub, name="paper_card",
                max_tokens=14000, prompt=f"{context}{task}\n\n{rules}", strict=_strict(self.smart), thinking=not _strict(self.smart),
            )
            data.update(part)
        data = conform(data, CARD_SCHEMA)
        data["verification"] = verify_tree(data, lookup)
        self.store.save_analysis(paper_id, "card", data, self.smart)
        return data

    # ---------------------------------------------------------------- stage 2
    def paragraphs(self, paper_id: str, on_progress: Callable[[str], None] | None = None) -> dict:
        paper, paras, system, lookup = self._ctx(paper_id)
        card = self.store.analysis(paper_id, "card") or {}
        ids = _card_index(card)
        targets = [p for p in paras if p["kind"] in ("body", "caption") and len(p["text"]) > 40]
        existing = {x["pid"]: x for x in (self.store.analysis(paper_id, "paragraphs") or {}).get("paragraphs", [])}
        out = dict(existing)
        n = settings.paragraph_batch
        todo = [p for p in targets if p["pid"] not in existing]
        queue = [todo[i : i + n] for i in range(0, len(todo), n)]
        while queue:
            batch = queue.pop(0)
            if on_progress:
                on_progress(f"explaining paragraphs {len(out) + 1}-{len(out) + len(batch)} of {len(targets)}")
            try:
                r = self.llm.structured(
                    purpose="paragraphs", model=settings.fast_model, system=system, schema=PARA_SCHEMA, name="explain",
                    max_tokens=8000, thinking=False,
                    prompt=(
                        f"Card ids for linking: {json.dumps(ids)}\n\n"
                        f"Explain ONLY these paragraphs, in order: {', '.join(p['pid'] for p in batch)}.\n"
                        "Write for a strong PhD student outside this sub-field: unpack every symbol and acronym, say what "
                        "the paragraph is doing in the paper's argument, and link it to the card ids it supports. "
                        "Keep each explanation to 2-4 sentences and why_it_matters to one sentence."
                    ),
                )
            except ValueError as e:
                if "max_tokens" in str(e) and len(batch) > 1:  # dense paragraphs: split the batch and retry
                    half = len(batch) // 2
                    queue[:0] = [batch[:half], batch[half:]]
                    continue
                raise
            for x in conform(r, PARA_SCHEMA)["paragraphs"]:
                if x.get("pid") in lookup:
                    out[x["pid"]] = x
            # save after every batch so a crash or budget stop keeps progress
            self.store.save_analysis(paper_id, "paragraphs", {"paragraphs": list(out.values())}, settings.fast_model)
        data = {"paragraphs": sorted(out.values(), key=lambda x: int(x["pid"][1:]))}
        self.store.save_analysis(paper_id, "paragraphs", data, settings.fast_model)
        return data

    # ---------------------------------------------------------------- stage 3
    def threads(self, paper_id: str) -> dict:
        paper, paras, system, lookup = self._ctx(paper_id)
        card = self.store.analysis(paper_id, "card") or {}
        data = self.llm.structured(
            purpose="threads", model=self.smart, system=system, schema=THREAD_SCHEMA, name="threads",
            max_tokens=14000, strict=_strict(self.smart), thinking=not _strict(self.smart),
            prompt=(
                f"Paper card (for ids): {json.dumps(_card_index(card))}\n\n"
                "Build 4-6 threads that trace how ideas run through the paper. Always include one "
                "hypothesis-to-evidence thread per hypothesis (question -> hypothesis -> method -> experiment -> result -> "
                "interpretation -> limitation), plus threads for the central concepts. Steps must reference real pids in "
                "reading order, at most 8 steps per thread, each note one sentence. In the verdict (2-3 sentences), be blunt about "
                "whether the evidence actually establishes the claim."
            ),
        )
        data = conform(data, THREAD_SCHEMA)
        for t in data.get("threads", []):
            t["steps"] = [s for s in t.get("steps", []) if s.get("pid") in lookup]
        self.store.save_analysis(paper_id, "threads", data, self.smart)
        return data

    # ---------------------------------------------------------------- stage 4
    def graph(self, paper_id: str) -> dict:
        paper, paras, system, lookup = self._ctx(paper_id)
        others = [p["title"] for p in self.store.papers() if p["id"] != paper_id]
        data = None
        for n_ent, n_rel in ((25, 35), (14, 18)):  # retry smaller if the output overruns
            try:
                data = self.llm.structured(
                    purpose="graph", model=self.smart, system=system, schema=GRAPH_SCHEMA, name="graph",
                    max_tokens=12000, strict=_strict(self.smart), thinking=not _strict(self.smart),
                    prompt=(
                        f"Extract the knowledge graph of this paper: the {n_ent // 2}-{n_ent} most important entities (methods, datasets, "
                        "benchmarks, metrics, models, bias types, key findings, limitations) and the relations between them. "
                        "Use canonical names so the same thing in different papers gets the same name (e.g. 'StereoSet', "
                        f"'GPTQ', 'equalized odds', 'knowledge distillation'). At most {n_rel} relations. Descriptions: one short "
                        "sentence. Exactly ONE evidence quote (6-20 words) per entity, relation and link.\n\n"
                        "Then link this paper to related papers from the library below. Only include a link if this paper's "
                        "text supports it (it cites, uses, extends, contradicts or addresses that work). At most 10 links.\n\n"
                        "Library:\n" + "\n".join(f"- {t}" for t in others)
                    ),
                )
                break
            except ValueError as e:
                if "max_tokens" not in str(e) or n_ent == 14:
                    raise
        data = conform(data, GRAPH_SCHEMA)
        data["verification"] = verify_tree(data, lookup)
        self.store.save_analysis(paper_id, "graph", data, self.smart)
        return data

    def run(self, paper_id: str, stages=("card", "graph", "threads", "paragraphs"), on_progress=None) -> None:
        names = {"card": "building paper card", "graph": "extracting knowledge graph",
                 "paragraphs": "explaining paragraphs", "threads": "tracing threads"}
        try:
            for st in stages:
                self.store.set_status(paper_id, "analyzing", names[st])
                if on_progress:
                    on_progress(names[st])
                if st == "paragraphs":
                    self.paragraphs(paper_id, on_progress=lambda m: self.store.set_status(paper_id, "analyzing", m))
                else:
                    getattr(self, st)(paper_id)
            self.store.set_status(paper_id, "analyzed", "")
        except Exception as e:  # keep partial results, surface the error
            log.exception("analysis failed")
            self.store.set_status(paper_id, "error", f"{type(e).__name__}: {e}")
            raise


def _strict(model: str) -> bool:
    """Constrained JSON decoding for small models (reliable JSON; their cache writes
    are cheap). Larger models use schema-in-prompt so the paper cache is shared."""
    return model.startswith("claude-haiku")


def conform(data, schema):
    """Fill anything the model left out with an empty value of the right type,
    so downstream code and the UI never meet a missing field."""
    t = schema.get("type")
    if t == "object":
        d = data if isinstance(data, dict) else {}
        for k, sub in schema.get("properties", {}).items():
            d[k] = conform(d.get(k), sub)
        return d
    if t == "array":
        return [conform(x, schema["items"]) for x in data] if isinstance(data, list) else []
    if t == "string":
        return data if isinstance(data, str) else ("" if data is None else str(data))
    if t == "boolean":
        return bool(data)
    if t == "integer":
        try:
            return int(data)
        except (TypeError, ValueError):
            return 0
    return data


def _card_index(card: dict) -> dict:
    """Compact id -> short text map passed to later stages."""
    out = {}
    for key in ("research_questions", "hypotheses", "experiments", "results"):
        for x in card.get(key, []) or []:
            if x.get("id"):
                out[x["id"]] = (x.get("text") or x.get("name") or x.get("claim") or "")[:140]
    for x in (card.get("methodology") or {}).get("components", []) or []:
        if x.get("id"):
            out[x["id"]] = x.get("name", "")
    return out
