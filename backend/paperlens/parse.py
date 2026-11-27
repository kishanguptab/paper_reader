"""PDF -> structured paragraphs.

Each paragraph keeps its page number and bounding box so every explanation,
card field and chat answer can point back to the exact spot in the PDF.

Design notes (the failures this guards against):
* Two-column layouts: PyMuPDF blocks are sorted column-first per page.
* Paragraphs split across columns/pages are re-joined when the first piece
  doesn't end a sentence and the next starts lowercase.
* Margin stamps (arXiv IDs), running headers/footers and page numbers are
  dropped by position and repetition.
* Each page gets a quality score; low-quality pages (scans, garbled
  extraction) are flagged so their explanations aren't trusted blindly.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pymupdf

HEADING_RE = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*\.?|[IVX]+\.|[A-H](?:\.\d+)*\.?)\s+)?"
    r"(abstract|introduction|related work|background|preliminaries|method(?:s|ology)?|"
    r"approach|experiments?(?:al setup)?|setup|results?|evaluation|analysis|discussion|"
    r"limitations?|conclusions?|future work|references|bibliography|appendix|"
    r"acknowledg(?:e)?ments?|ethic(?:s|al considerations).*|broader impact.*)\b",
    re.I,
)
NUMBERED_HEADING_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[A-H](?:\.\d+)+\.?)\s+[A-Z][^.!?]{1,80}$")
CAPTION_RE = re.compile(r"^(fig(?:ure)?\.?|table|algorithm)\s*\d+", re.I)
REF_HEADINGS = {"references", "bibliography"}


@dataclass
class Paragraph:
    idx: int
    page: int  # 1-based
    bbox: list[float]
    text: str
    kind: str = "body"  # body | heading | caption | reference | front
    section: str = ""

    @property
    def pid(self) -> str:
        return f"p{self.idx}"


@dataclass
class ParsedPaper:
    paper_id: str
    path: str
    title: str
    n_pages: int
    paragraphs: list[Paragraph] = field(default_factory=list)
    page_quality: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for p, src in zip(d["paragraphs"], self.paragraphs):
            p["pid"] = src.pid
        return d

    def body(self) -> list[Paragraph]:
        return [p for p in self.paragraphs if p.kind in ("body", "caption", "front")]


def paper_id_for(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _clean(text: str) -> str:
    text = text.replace("­", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks
    text = re.sub(r"\s*\n\s*", " ", text)
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff").replace("ﬃ", "ffi")
    return re.sub(r"\s{2,}", " ", text).strip()


def _page_quality(text: str) -> float:
    """Share of characters that look like real words. Scans / bad encodings score low."""
    if not text.strip():
        return 0.0
    words = re.findall(r"[A-Za-z]{2,}", text)
    alpha = sum(len(w) for w in words)
    return round(min(1.0, alpha / max(1, len(re.sub(r"\s", "", text))) / 0.75), 3)


def _looks_like_figure_text(t: str) -> bool:
    """Axis labels / chart legends extracted as text: mostly numbers, few verbs."""
    toks = t.split()
    if len(toks) < 4:
        return False
    numeric = sum(bool(re.fullmatch(r"[+\-−]?\d+(?:\.\d+)?%?", x)) for x in toks)
    return numeric / len(toks) > 0.35 and not re.search(r"[.!?]$", t)


def _is_heading(text: str, size: float, body_size: float, bold: bool) -> bool:
    if len(text) > 90 or text.endswith((".", ",", ";")) and not re.match(r"^\d", text):
        return False
    if HEADING_RE.match(text) and len(text.split()) <= 8:
        return True
    if NUMBERED_HEADING_RE.match(text) and (bold or size > body_size + 0.5):
        return True
    return bool(bold and size >= body_size + 0.8 and len(text.split()) <= 10)


def parse_pdf(path: str | Path) -> ParsedPaper:
    path = Path(path)
    doc = pymupdf.open(path)
    pid = paper_id_for(path)

    # --- pass 1: collect raw blocks with font info
    raw = []
    sizes: Counter = Counter()
    for pno, page in enumerate(doc, start=1):
        w, h = page.rect.width, page.rect.height
        d = page.get_text("dict", sort=False)
        for b in d["blocks"]:
            if b.get("type") != 0:
                continue
            spans = [s for l in b["lines"] for s in l["spans"] if s["text"].strip()]
            if not spans:
                continue
            # vertical text (arXiv margin stamp) -> skip
            if any(abs(l["dir"][1]) > 0.5 for l in b["lines"]):
                continue
            txt = "\n".join("".join(s["text"] for s in l["spans"]) for l in b["lines"])
            x0, y0, x1, y1 = b["bbox"]
            size = max(Counter({round(s["size"], 1): len(s["text"]) for s in spans}).items(), key=lambda kv: kv[1])[0]
            bold = sum(len(s["text"]) for s in spans if s["flags"] & 16) > 0.6 * sum(len(s["text"]) for s in spans)
            for s in spans:
                sizes[round(s["size"], 1)] += len(s["text"])
            raw.append(dict(page=pno, bbox=[x0, y0, x1, y1], text=txt, size=size, bold=bold, w=w, h=h))

    body_size = sizes.most_common(1)[0][0] if sizes else 10.0

    # running headers/footers: identical short text at top/bottom on >=3 pages
    edge = Counter(
        _clean(r["text"]).lower()
        for r in raw
        if (r["bbox"][1] < 0.07 * r["h"] or r["bbox"][3] > 0.93 * r["h"]) and len(r["text"]) < 120
    )
    repeated = {t for t, c in edge.items() if c >= 3}

    # --- pass 2: classify + order (two columns: left column first)
    page_texts: dict[int, list[str]] = {}
    blocks = []
    for r in raw:
        t = _clean(r["text"])
        if not t:
            continue
        top_or_bottom = r["bbox"][1] < 0.07 * r["h"] or r["bbox"][3] > 0.93 * r["h"]
        if top_or_bottom and (re.fullmatch(r"\d{1,3}", t) or t.lower() in repeated):
            continue
        if re.fullmatch(r"\d{1,3}", t):
            continue
        page_texts.setdefault(r["page"], []).append(t)
        mid = r["w"] / 2
        col = 0 if r["bbox"][2] <= mid + 10 else (1 if r["bbox"][0] >= mid - 10 else 0)
        full_width = r["bbox"][0] < mid - 10 and r["bbox"][2] > mid + 10
        r.update(text=t, col=-1 if full_width else col)
        blocks.append(r)

    def order_key(r):
        # full-width blocks above columns stay on top; otherwise column-major
        return (r["page"], 0 if r["col"] == -1 and r["bbox"][1] < 200 else 1, max(r["col"], 0), r["bbox"][1])

    blocks.sort(key=order_key)

    paragraphs: list[Paragraph] = []
    section = "Front matter"
    in_refs = False
    seen_abstract = False
    for r in blocks:
        t = r["text"]
        # "1\nIntroduction" style headings were joined as "1 Introduction"
        if _is_heading(t, r["size"], body_size, r["bold"]):
            if not seen_abstract and r["page"] == 1 and not HEADING_RE.match(t):
                paragraphs.append(Paragraph(len(paragraphs), r["page"], r["bbox"], t, "front", "Front matter"))
                continue
            section = re.sub(r"^[\dIVXA-H.]+\s+", "", t).strip()
            section = section.title() if section.isupper() else section
            low = section.lower()
            in_refs = low in REF_HEADINGS
            if low.startswith("abstract"):
                seen_abstract = True
            paragraphs.append(Paragraph(len(paragraphs), r["page"], r["bbox"], t, "heading", section))
            continue
        # heading glued onto first paragraph: "Abstract We propose..."
        m = re.match(r"^(Abstract)[\s.:—-]+(.{40,})", t)
        if m and not seen_abstract:
            seen_abstract = True
            section = "Abstract"
            paragraphs.append(Paragraph(len(paragraphs), r["page"], r["bbox"], "Abstract", "heading", section))
            t = m.group(2)
        if in_refs:
            kind = "reference"
        elif CAPTION_RE.match(t):
            kind = "caption"
        elif _looks_like_figure_text(t):
            kind = "figure_text"
        elif section == "Front matter":
            kind = "front"
        else:
            kind = "body"
        if len(t) < 25 and kind == "body":  # equation fragments, stray labels
            if paragraphs and paragraphs[-1].kind == "body":
                paragraphs[-1].text += " " + t
            continue
        prev = paragraphs[-1] if paragraphs else None
        if (
            prev is not None
            and kind == "body"
            and prev.kind == "body"
            and not re.search(r"[.!?:)\]”\"]$", prev.text)
            and re.match(r"^[a-z(\[]", t)
        ):
            prev.text += " " + t  # paragraph continues across column/page
            continue
        paragraphs.append(Paragraph(len(paragraphs), r["page"], [round(v, 1) for v in r["bbox"]], t, kind, section))

    meta_title = (doc.metadata or {}).get("title") or ""
    title = meta_title.strip() if len(meta_title.strip()) > 8 else ""
    if not title:
        first = [r for r in raw if r["page"] == 1]
        if first:
            title = _clean(max(first[:8], key=lambda r: r["size"])["text"])
    if not title:
        title = path.stem

    quality = [_page_quality(" ".join(page_texts.get(i, []))) for i in range(1, len(doc) + 1)]
    return ParsedPaper(pid, str(path), title, len(doc), paragraphs, quality)


if __name__ == "__main__":
    import sys

    pp = parse_pdf(sys.argv[1])
    print(pp.title, pp.n_pages, "pages", len(pp.paragraphs), "paragraphs", "quality", pp.page_quality)
    for p in pp.paragraphs[: int(sys.argv[2]) if len(sys.argv) > 2 else 40]:
        print(f"[{p.pid} p{p.page} {p.kind:9} {p.section[:18]:18}] {p.text[:110]}")
