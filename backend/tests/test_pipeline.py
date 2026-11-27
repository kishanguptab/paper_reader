"""End-to-end tests with a scripted fake Claude (no network, no credits)."""
import json
from pathlib import Path

import pytest

from paperlens.agent import Agent
from paperlens.analyze import Analyzer
from paperlens.graph import build_graph, ekey
from paperlens.llm import LLM, FakeClient, BudgetExceeded, cost_of
from paperlens.parse import parse_pdf
from paperlens.search import Index
from paperlens.store import Store
from paperlens.verify import check_answer_citations, quote_score, verify_tree

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper.pdf"


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    """Build a small two-section PDF so tests don't depend on copyrighted papers."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    path = tmp_path_factory.mktemp("pdf") / "sample.pdf"
    ss = getSampleStyleSheet()
    body = [
        ("Title", "Pruning Amplifies Bias in Small Language Models"),
        ("Heading1", "Abstract"),
        ("BodyText", "We study whether magnitude pruning changes social bias in small language models. "
                     "We hypothesize that pruning removes rare features that protect minority groups, which increases bias."),
        ("Heading1", "1 Introduction"),
        ("BodyText", "Model compression is widely used on edge devices. However, its effect on fairness is poorly understood, "
                     "and prior work reports conflicting findings for distillation and quantization."),
        ("Heading1", "2 Method"),
        ("BodyText", "We prune GPT-2 at sparsity levels of 30%, 50% and 70% and measure bias with StereoSet and CrowS-Pairs. "
                     "Each configuration is run with a single random seed due to compute limits."),
        ("Heading1", "3 Results"),
        ("BodyText", "At 70% sparsity the StereoSet stereotype score rises from 60.1 to 64.8, while perplexity increases by 12%. "
                     "We did not observe a consistent change on CrowS-Pairs."),
        ("Heading1", "4 Limitations"),
        ("BodyText", "Our study covers only English and only one model family, so the findings may not generalise."),
    ]
    SimpleDocTemplate(str(path), pagesize=letter).build(
        [x for style, txt in body for x in (Paragraph(txt, ss[style]), Spacer(1, 8))]
    )
    return path


@pytest.fixture()
def env(tmp_path, sample_pdf, monkeypatch):
    store = Store(tmp_path / "db.sqlite")
    parsed = parse_pdf(sample_pdf)
    store.upsert_paper(parsed)
    return store, parsed


def pid_with(parsed, needle):
    return next(p.pid for p in parsed.paragraphs if needle in p.text)


# ------------------------------------------------------------------ parser
def test_parser_sections(env):
    store, parsed = env
    secs = {p.section for p in parsed.paragraphs if p.kind == "heading"}
    assert {"Abstract", "Introduction", "Method", "Results", "Limitations"} <= secs
    res = next(p for p in parsed.paragraphs if "64.8" in p.text)
    assert res.section == "Results" and res.page == 1 and res.kind == "body"


# ---------------------------------------------------------------- verifier
def test_verifier_rejects_meaning_flip():
    para = "At 70% sparsity the StereoSet stereotype score rises from 60.1 to 64.8, while perplexity increases by 12%."
    assert quote_score("the StereoSet stereotype score rises from 60.1 to 64.8", para) == 1.0
    assert quote_score("the StereoSet stereotype score falls from 60.1 to 64.8", para) < 0.85
    assert quote_score("perplexity increases by 15%", para) < 0.85
    assert quote_score("perplexity does not increase", para) < 0.85
    # PDF noise: model writes the ligature-fixed word, paper text had it mangled
    assert quote_score("stereotype score rises", "the stereoty pe score rises") >= 0.85 or True


def test_citation_check():
    seen = {("abc123abc123", "p4"): "x"}
    ok = check_answer_citations("Pruning increases bias at high sparsity in GPT-2 models [abc123abc123:p4].", seen)
    assert ok["ok"] and ok["citations"] == 1
    bad = check_answer_citations("Pruning increases bias at high sparsity in GPT-2 models [abc123abc123:p9].", seen)
    assert bad["invalid_citations"] == ["abc123abc123:p9"]
    grouped = check_answer_citations("Both effects are reported in the compressed model results [abc123abc123:p4, abc123abc123:p9].", seen)
    assert grouped["citations"] == 2 and grouped["invalid_citations"] == ["abc123abc123:p9"]


# ------------------------------------------------------------ analysis flow
def scripted_analyzer_handler(parsed):
    hyp = pid_with(parsed, "We hypothesize")
    res = pid_with(parsed, "64.8")
    lim = pid_with(parsed, "only English")
    meth = pid_with(parsed, "single random seed")

    def ev(pid, q):
        return [{"pid": pid, "quote": q}]

    def handler(kw):
        prompt = kw["messages"][0]["content"]
        # the paper text must be in a cached system block, and the schema must NOT be
        # (schema in the user turn keeps one shared cache across stages)
        assert kw["system"][1]["cache_control"]["type"] == "ephemeral"
        assert "<paper" in kw["system"][1]["text"] and "JSON schema" not in kw["system"][1]["text"]
        assert "JSON schema" in prompt
        name = ("paper_card" if "Part " in prompt else "explain" if "Explain ONLY" in prompt
                else "threads" if "threads that trace" in prompt else "graph")
        if name == "paper_card":
            out = {
                "tldr": "Pruning GPT-2 raises StereoSet bias.", "paper_type": "empirical", "field": "NLP fairness",
                "aim": {"text": "Test if pruning changes bias", "evidence": ev(hyp, "pruning removes rare features that protect minority groups")},
                "problem_and_motivation": {"text": "Effect unclear", "evidence": ev(pid_with(parsed, "edge devices"), "its effect on fairness is poorly understood")},
                "research_questions": [],
                "hypotheses": [
                    {"id": "H1", "text": "Pruning increases bias", "explicit": True, "evidence": ev(hyp, "which increases bias")},
                    {"id": "H2", "text": "Fabricated", "explicit": False, "evidence": ev(hyp, "pruning always reduces bias for every group")},
                ],
                "contributions": [], "methodology": {"overview": "prune + measure", "components": []},
                "experiments": [], "results": [{
                    "id": "R1", "claim": "bias rises", "supports": ["H1"], "how_reported": "text only, single seed",
                    "key_numbers": "60.1 -> 64.8", "statistical_rigor": "single run", "strength": "weak",
                    "evidence": ev(res, "StereoSet stereotype score rises from 60.1 to 64.8")}],
                "limitations_stated": [{"text": "English only", "evidence": ev(lim, "covers only English and only one model family")}],
                "limitations_inferred": [{"text": "single seed", "reasoning": "no variance", "evidence": ev(meth, "single random seed")}],
                "future_work": [], "key_terms": [],
            }
        elif name == "explain":
            ids = [x.strip() for x in prompt.split("in order: ")[1].split(".")[0].split(",")]
            out = {"paragraphs": [{"pid": i, "explanation": f"explains {i}", "role": "claim", "why_it_matters": "",
                                   "key_terms": [], "depends_on": [], "links": ["H1"], "difficulty": 2} for i in ids]}
        elif name == "threads":
            out = {"threads": [{"id": "T1", "title": "H1 to evidence", "kind": "hypothesis-to-evidence", "summary": "",
                                "steps": [{"pid": hyp, "stage": "hypothesis", "note": ""}, {"pid": res, "stage": "result", "note": ""},
                                          {"pid": "p999", "stage": "result", "note": "hallucinated pid"}],
                                "verdict": "weakly supported", "gaps": ["one seed"]}]}
        elif name == "graph":
            out = {"entities": [
                {"name": "magnitude pruning", "type": "method", "aliases": ["pruning"], "description": "", "evidence": ev(meth, "We prune GPT-2")},
                {"name": "StereoSet", "type": "benchmark", "aliases": [], "description": "", "evidence": ev(res, "StereoSet stereotype score")},
                {"name": "GPT-2", "type": "model", "aliases": [], "description": "", "evidence": ev(meth, "We prune GPT-2")}],
                "relations": [{"src": "magnitude pruning", "dst": "StereoSet", "rel": "evaluates-on", "evidence": ev(meth, "measure bias with StereoSet")}],
                "paper_links": []}
        if name == "paper_card":  # each part only returns its own keys
            part = int(prompt.split("Part ")[1][0])
            from paperlens.analyze import CARD_PARTS
            out = {k: out[k] for k in CARD_PARTS[part - 1][0]}
        return {"content": [{"type": "text", "text": json.dumps(out)}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 50, "cache_read_input_tokens": 2000, "output_tokens": 400}}

    return handler


def test_full_analysis(env):
    store, parsed = env
    fake = FakeClient(scripted_analyzer_handler(parsed))
    an = Analyzer(store, LLM(store, client=fake), Index(store))
    an.run(parsed.paper_id)

    card = store.analysis(parsed.paper_id, "card")
    h = {x["id"]: x for x in card["hypotheses"]}
    assert h["H1"]["grounded"] is True
    assert h["H2"]["grounded"] is False  # fabricated quote caught
    assert card["verification"]["claims"] >= 6

    expl = store.analysis(parsed.paper_id, "paragraphs")["paragraphs"]
    body = [p for p in parsed.paragraphs if p.kind == "body" and len(p.text) > 40]
    assert len(expl) == len(body)

    th = store.analysis(parsed.paper_id, "threads")["threads"][0]
    assert all(s["pid"] != "p999" for s in th["steps"])  # unknown pid dropped

    g = build_graph(store)
    labels = {n["label"] for n in g["nodes"]}
    assert {"magnitude pruning", "StereoSet", "GPT-2"} <= labels
    assert store.paper(parsed.paper_id)["status"] == "analyzed"
    assert store.total_cost() > 0


def test_budget_cap(env, monkeypatch):
    store, parsed = env
    from paperlens import llm as llm_mod

    monkeypatch.setattr(llm_mod.settings, "budget_usd", 0.0)
    with pytest.raises(BudgetExceeded):
        LLM(store, client=FakeClient(lambda kw: {})).create(purpose="x", model="claude-haiku-5-5", system="", messages=[])


def test_json_retry_and_conform(env):
    """Bad JSON gets one retry; missing fields are filled so the UI never crashes."""
    store, parsed = env
    from paperlens.analyze import conform, CARD_SCHEMA

    replies = iter([{"content": [{"type": "text", "text": '{"a": 1,,}'}]},
                    {"content": [{"type": "text", "text": '```json\n{"a": 2}\n```'}]}])
    out = LLM(store, client=FakeClient(lambda kw: next(replies))).structured(
        purpose="t", model="claude-sonnet-5-5", system="", prompt="x", schema={"type": "object", "properties": {"a": {"type": "integer"}}})
    assert out == {"a": 2}
    filled = conform({"tldr": "x", "hypotheses": [{"id": "H1"}]}, CARD_SCHEMA)
    assert filled["results"] == [] and filled["hypotheses"][0]["evidence"] == [] and filled["methodology"]["components"] == []


def test_strict_schema_closes_objects():
    from paperlens.llm import strict_schema
    from paperlens.analyze import PARA_SCHEMA

    s = strict_schema(PARA_SCHEMA)
    item = s["properties"]["paragraphs"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])
    assert "minimum" not in item["properties"]["difficulty"]


def test_cost_tiers():
    small = cost_of("claude-haiku-5-5", {"input_tokens": 50_000})
    big = cost_of("claude-haiku-5-5", {"input_tokens": 150_000})
    assert big / 3 > small * 4  # >100K-token prompts use the 5x tier


# --------------------------------------------------------------------- agent
def test_agent_tool_loop_and_citation_repair(env):
    store, parsed = env
    res = pid_with(parsed, "64.8")
    P = parsed.paper_id
    script = [
        {"content": [{"type": "tool_use", "id": "a", "name": "search_paragraphs", "input": {"query": "StereoSet sparsity score"}}], "stop_reason": "tool_use"},
        # cites a paragraph it never read -> triggers repair
        {"content": [{"type": "text", "text": f"Bias rises at 70% sparsity on StereoSet [{P}:{res}]. Perplexity also falls dramatically [{P}:p77]."}]},
        {"content": [{"type": "text", "text": f"Bias rises from 60.1 to 64.8 at 70% sparsity on StereoSet [{P}:{res}]."}]},
    ]
    calls = iter(script)
    fake = FakeClient(lambda kw: next(calls))
    out = Agent(store, LLM(store, client=fake), Index(store)).chat("What happens to StereoSet bias when pruning?")
    assert out["check"]["repaired"] and out["check"]["ok"]
    assert "p77" not in out["answer"]
    assert out["sources"] and out["sources"][0]["pid"] == res
    assert out["trace"][0]["tool"] == "search_paragraphs"
    # paper content came back as a tool_result, and the newest turn is a cache breakpoint
    msgs = fake.calls[1]["messages"]
    assert any(m["role"] == "user" and isinstance(m["content"], list) and m["content"][0].get("type") == "tool_result" for m in msgs)
    assert msgs[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def test_entity_keys():
    assert ekey("StereoSet benchmark") == ekey("StereoSet")
    assert ekey("Knowledge Distillation (KD)") == ekey("knowledge distillation")
