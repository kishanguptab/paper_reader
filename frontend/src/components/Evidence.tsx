import { Evidence as Ev, Grounded } from "../api";

/** Evidence chips: ✓ verified quote, ⚠ quote not found in the cited paragraph. */
export function Evidence({ item, onOpen }: { item: Grounded; onOpen: (pid: string) => void }) {
  if (!item.evidence?.length) return <span className="ev none" title="No evidence given">no evidence</span>;
  return (
    <span className="evs">
      {item.evidence.map((e: Ev, i) => (
        <button
          key={i}
          className={`ev ${e.verified ? "ok" : "bad"}`}
          title={`${e.verified ? "Verified" : "NOT found in paragraph"} (match ${e.score ?? "?"}): “${e.quote}”`}
          onClick={() => onOpen(e.pid)}
        >
          {e.verified ? "✓" : "⚠"} {e.pid}
        </button>
      ))}
    </span>
  );
}

export function GroundBadge({ item }: { item: Grounded }) {
  if (item.grounded === undefined) return null;
  return item.grounded ? null : <span className="pill warn small" title="None of the evidence quotes were found in the paper. Treat as unverified.">unverified</span>;
}

/** Render [paper:pid] citations inside markdown-ish answer text as clickable chips. */
export function linkCitations(text: string): string {
  // handles [id:p4] and grouped [id:p4, id:p5] / [id:p4; id2:p9]
  return text.replace(/\[((?:[0-9a-f]{12}:p\d+\s*[,;]?\s*)+)\]/g, (_, group: string) =>
    Array.from(group.matchAll(/([0-9a-f]{12}):(p\d+)/g)).map((m) => `[${m[2]}](#cite/${m[1]}/${m[2]})`).join(" ")
  );
}
