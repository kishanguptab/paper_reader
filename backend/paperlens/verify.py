"""Evidence verification: the core failure PaperLens is designed against.

LLMs will happily produce a plausible "limitation" or "hypothesis" a paper
never states, and cite a paragraph that doesn't support it. Every claim the
model makes must carry (pid, quote) evidence; this module checks that the
quote really occurs in that paragraph. Claims that fail are kept but marked
unverified so the UI shows them differently and the chat agent must retract
or re-ground them.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s%.]")


def norm(s: str) -> str:
    s = s.lower().replace("ﬁ", "fi").replace("ﬂ", "fl").replace("’", "'").replace("“", '"').replace("”", '"')
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


MEANING_WORDS = {
    "not", "no", "never", "none", "nor", "without", "cannot", "can't", "don't", "doesn't", "didn't", "isn't", "aren't",
    "increase", "increases", "increased", "decrease", "decreases", "decreased", "reduce", "reduces", "reduced",
    "improve", "improves", "improved", "worse", "better", "higher", "lower", "more", "less", "always", "only",
    "all", "some", "most", "fewer", "positive", "negative", "significant", "insignificant",
}


def _tok_close(a: str, b: str) -> bool:
    """Same word up to PDF-extraction noise (ligatures, a dropped/merged char)."""
    if a == b:
        return True
    if a in MEANING_WORDS or b in MEANING_WORDS or a.replace(".", "").isdigit() or b.replace(".", "").isdigit():
        return False
    return len(a) > 3 and len(b) > 3 and SequenceMatcher(None, a, b).ratio() >= 0.8


def _token_score(qt: list[str], pt: list[str]) -> float:
    """Best alignment of quote tokens inside paragraph tokens. Word substitutions
    that change meaning (negations, directions, numbers) make the score 0;
    character noise inside a word is tolerated."""
    n = len(qt)
    if n == 0 or not pt:
        return 0.0
    best = 0.0
    first = {qt[0], qt[1] if n > 1 else qt[0]}
    starts = [i for i, t in enumerate(pt) if t in first or _tok_close(t, qt[0])] or range(len(pt))
    for s in starts:
        s = max(0, s - 1)
        window = pt[s : s + n + 3]
        sm = SequenceMatcher(None, qt, window, autojunk=False)
        matched, bad = 0, 0
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                matched += i2 - i1
            elif op == "replace" and (i2 - i1) == (j2 - j1) and all(_tok_close(a, b) for a, b in zip(qt[i1:i2], window[j1:j2])):
                matched += i2 - i1
            elif op == "insert":  # extra tokens in paper (e.g. split word) — fine unless meaningful
                if i1 == 0 or i1 == n:  # window padding before/after the quote
                    continue
                if any(t in MEANING_WORDS for t in window[j1:j2]) or (j2 - j1) > 2:
                    bad += 1
            else:
                bad += 1
        if bad == 0:
            # tolerate quote truncated at window edge
            best = max(best, matched / n)
        if best >= 0.999:
            break
    return best


def quote_score(quote: str, paragraph: str) -> float:
    """1.0 for exact (normalised) substring; otherwise a strict token alignment
    that forgives character-level extraction noise but not word changes."""
    q, p = norm(quote), norm(paragraph)
    if not q:
        return 0.0
    if q in p:
        return 1.0
    pieces = [x.strip() for x in re.split(r"\.\.\.|…", quote) if x.strip()]
    if len(pieces) > 1:
        scores = [quote_score(x, paragraph) for x in pieces]
        return min(scores) * 0.98
    return _token_score(q.split(), p.split())


def verify_evidence(evidence: list[dict], paragraphs: dict[str, str], threshold: float = 0.85) -> list[dict]:
    """Annotate each {pid, quote} with score + verified. Unknown pid => 0."""
    out = []
    for ev in evidence or []:
        pid = str(ev.get("pid", "")).strip("[] ")
        quote = ev.get("quote", "")
        text = paragraphs.get(pid)
        score = quote_score(quote, text) if text else 0.0
        out.append({**ev, "pid": pid, "score": round(score, 3), "verified": score >= threshold})
    return out


def verify_tree(obj, paragraphs: dict[str, str], stats: dict | None = None):
    """Walk any JSON structure; wherever a dict has an 'evidence' list, verify it
    and set item['grounded'] = at least one verified quote."""
    if stats is None:
        stats = {"claims": 0, "grounded": 0, "quotes": 0, "quotes_ok": 0}
    if isinstance(obj, dict):
        if isinstance(obj.get("evidence"), list):
            obj["evidence"] = verify_evidence(obj["evidence"], paragraphs)
            stats["claims"] += 1
            stats["quotes"] += len(obj["evidence"])
            ok = sum(e["verified"] for e in obj["evidence"])
            stats["quotes_ok"] += ok
            obj["grounded"] = ok > 0
            stats["grounded"] += int(ok > 0)
        for k, v in obj.items():
            if k != "evidence":
                verify_tree(v, paragraphs, stats)
    elif isinstance(obj, list):
        for v in obj:
            verify_tree(v, paragraphs, stats)
    return stats


CITE_RE = re.compile(r"(?<=[\[,;\s])([0-9a-f]{12}):(p\d+)(?=[\],;\s])")  # single or grouped [a:p1, b:p2]


def check_answer_citations(answer: str, seen: dict[tuple[str, str], str]) -> dict:
    """For chat: every [paper:pid] cited must be a paragraph the agent actually
    retrieved this turn. Sentences with factual content but no citation are
    counted so the UI can show an honesty meter."""
    cites = CITE_RE.findall(answer)
    bad = [f"{a}:{b}" for a, b in cites if (a, b) not in seen]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", answer) if len(s.split()) > 6]
    uncited = [s for s in sentences if not CITE_RE.search(s) and not s.lower().startswith(("i ", "the papers do not", "i could not", "not stated"))]
    return {
        "citations": len(cites),
        "invalid_citations": bad,
        "uncited_sentences": len(uncited),
        "sentences": len(sentences),
        "ok": not bad,
    }
