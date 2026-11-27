from __future__ import annotations

import logging
from pathlib import Path

from .parse import parse_pdf, paper_id_for

log = logging.getLogger("paperlens")


def ingest_file(store, path: Path) -> str:
    pp = parse_pdf(path)
    store.upsert_paper(pp)
    return pp.paper_id


def ingest_folder(store, folder: Path) -> int:
    """Parse every PDF not yet in the store (matched by content hash). Returns count added."""
    known = {p["id"]: p["path"] for p in store.papers()}
    n = 0
    for f in sorted(Path(folder).expanduser().rglob("*.pdf")):
        try:
            pid = paper_id_for(f)
            if pid in known:
                if known[pid] != str(f):  # same paper (content hash), new location: re-link, keep analyses
                    store.relink(pid, str(f))
                continue
            ingest_file(store, f)
            n += 1
            log.info("ingested %s", f.name)
        except Exception as e:  # a broken PDF must not stop the library
            log.warning("could not parse %s: %s", f.name, e)
    return n
