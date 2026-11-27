import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { useNavigate } from "react-router-dom";
import { api, GraphData } from "../api";
import { useApp } from "../App";
import { Legend } from "./Paper";
import { useSpreadGraph } from "../components/useSpreadGraph";

export default function GraphPage() {
  const [g, setG] = useState<GraphData | null>(null);
  const [mode, setMode] = useState<"papers" | "all">("all");
  const [minPapers, setMinPapers] = useState(2);
  const [sel, setSel] = useState<any>(null);
  const [query, setQuery] = useState("");
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(1000);
  const { titleOf } = useApp();
  const nav = useNavigate();

  useEffect(() => { api.graph().then(setG); }, []);
  useEffect(() => { if (wrap.current) setW(wrap.current.clientWidth); }, [g]);

  const fg = useSpreadGraph([g, mode, minPapers], mode === "papers" ? -400 : -180, mode === "papers" ? 120 : 60);
  const data = useMemo(() => {
    if (!g) return { nodes: [], links: [] };
    let nodes = g.nodes.filter((n) => n.type === "paper" || n.papers.length >= minPapers);
    if (mode === "papers") {
      nodes = g.nodes.filter((n) => n.type === "paper");
      const links = g.shared
        .filter((s) => s.entities.length >= minPapers)
        .map((s) => ({ source: `paper:${s.a}`, target: `paper:${s.b}`, rel: `${s.entities.length} shared`, value: s.entities.length, entities: s.entities }));
      const paperLinks = g.edges.filter((e) => e.src.startsWith("paper:") && e.dst.startsWith("paper:")).map((e) => ({ source: e.src, target: e.dst, rel: e.rel, explicit: true }));
      return { nodes: nodes.map((n) => ({ ...n })), links: [...links, ...paperLinks] };
    }
    const ids = new Set(nodes.map((n) => n.id));
    const seen = new Set<string>();
    const links = g.edges
      .filter((e) => ids.has(e.src) && ids.has(e.dst))
      .filter((e) => { const k = `${e.src}|${e.dst}|${e.rel}`; if (seen.has(k)) return false; seen.add(k); return true; })
      .map((e) => ({ source: e.src, target: e.dst, rel: e.rel, grounded: e.grounded }));
    return { nodes: nodes.map((n) => ({ ...n })), links };
  }, [g, mode, minPapers]);

  const q = query.toLowerCase();
  if (!g) return <div className="page"><p className="muted">Building graph…</p></div>;
  const entityCount = g.nodes.filter((n) => n.type !== "paper").length;

  return (
    <div className="page graph-page">
      <div className="graph-tools">
        <h1>Knowledge graph</h1>
        <p className="muted">{entityCount} concepts, methods, datasets and metrics across {g.nodes.length - entityCount} papers. Built from each paper's analysis; papers without a card don't appear yet.</p>
        <div className="row-tools">
          <div className="seg">
            <button className={mode === "all" ? "on" : ""} onClick={() => setMode("all")}>Papers + concepts</button>
            <button className={mode === "papers" ? "on" : ""} onClick={() => setMode("papers")}>Paper-to-paper</button>
          </div>
          <label>{mode === "all" ? "Concepts in at least" : "Papers sharing at least"} <input type="number" min={1} max={10} value={minPapers} onChange={(e) => setMinPapers(Number(e.target.value))} /> {mode === "all" ? "papers" : "concepts"}</label>
          <input className="search" placeholder="Highlight…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
      </div>
      <div className="graph-wrap big" ref={wrap}>
        <ForceGraph2D
          ref={fg.ref}
          onEngineStop={fg.onEngineStop}
          graphData={data as any}
          width={w}
          height={640}
          cooldownTicks={120}
          linkColor={(l: any) => (l.explicit ? "rgba(37,99,235,.7)" : l.grounded === false ? "rgba(220,38,38,.25)" : "rgba(100,116,139,.25)")}
          linkWidth={(l: any) => (l.value ? Math.min(6, l.value / 2) : l.explicit ? 2 : 1)}
          linkDirectionalArrowLength={(l: any) => (l.explicit ? 5 : 0)}
          onNodeClick={(n: any) => setSel(n)}
          nodeCanvasObject={(n: any, ctx, scale) => {
            const isPaper = n.type === "paper";
            const r = isPaper ? 7 : 3 + Math.min(6, n.papers.length);
            const hit = q && n.label.toLowerCase().includes(q);
            ctx.beginPath();
            if (isPaper) ctx.rect(n.x - r, n.y - r, 2 * r, 2 * r); else ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
            ctx.fillStyle = q && !hit ? "rgba(148,163,184,.35)" : n.color;
            ctx.fill();
            if (hit) { ctx.strokeStyle = "#f59e0b"; ctx.lineWidth = 2 / scale; ctx.stroke(); }
            if (scale > 1.2 || hit || (!isPaper && n.papers.length >= 4)) {
              const fs = (isPaper ? 9 : 10) / scale;
              ctx.font = `${fs}px Inter, system-ui`; ctx.fillStyle = "#1e293b"; ctx.textAlign = "center";
              const label = isPaper ? n.label.slice(0, 40) + (n.label.length > 40 ? "…" : "") : n.label;
              ctx.fillText(label, n.x, n.y + r + fs);
            }
          }}
        />
        <Legend />
        {sel && (
          <div className="node-card">
            <button className="x" onClick={() => setSel(null)}>×</button>
            <div className="eyebrow">{sel.type}</div>
            <h4>{sel.label}</h4>
            {sel.description && <p>{sel.description}</p>}
            {sel.type === "paper" ? (
              <button className="btn small primary" onClick={() => nav(`/paper/${sel.papers[0]}`)}>Open paper</button>
            ) : (
              <>
                <p className="muted small">Appears in {sel.papers.length} paper(s):</p>
                <ul className="mini">{sel.papers.map((p: string) => <li key={p}><a onClick={() => nav(`/paper/${p}?tab=graph`)}>{titleOf(p)}</a></li>)}</ul>
              </>
            )}
          </div>
        )}
      </div>

      <section className="two">
        <div className="block">
          <h3>Strongest paper connections</h3>
          {g.shared.slice(0, 12).map((s, i) => (
            <div key={i} className="row">
              <span className="tag">{s.entities.length}</span>
              <div className="row-body">
                <a onClick={() => nav(`/paper/${s.a}`)}>{titleOf(s.a)}</a> ↔ <a onClick={() => nav(`/paper/${s.b}`)}>{titleOf(s.b)}</a>
                <div className="muted small">{s.entities.slice(0, 8).join(" · ")}</div>
              </div>
            </div>
          ))}
          {!g.shared.length && <p className="muted">Analyze a few papers to see connections.</p>}
        </div>
        <div className="block">
          <h3>Possible duplicates to review</h3>
          <p className="muted small">Similar names that were <em>not</em> merged automatically. A wrong merge would invent connections between papers, so these stay separate until you decide.</p>
          {g.review.slice(0, 20).map((r, i) => (
            <div key={i} className="row"><span className="tag ghost">{r.score}</span><div className="row-body">{r.a_label} ≟ {r.b_label}</div></div>
          ))}
          {!g.review.length && <p className="muted">Nothing to review.</p>}
        </div>
      </section>
    </div>
  );
}
