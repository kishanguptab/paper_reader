import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import ForceGraph2D from "react-force-graph-2d";
import { api, Card, Explanation, PaperFull, Paragraph, pdfUrl, Thread } from "../api";
import { useApp } from "../App";
import { Evidence, GroundBadge } from "../components/Evidence";
import { useSpreadGraph } from "../components/useSpreadGraph";

const TABS = [
  ["overview", "Overview"],
  ["read", "Read paragraph by paragraph"],
  ["threads", "Threads"],
  ["graph", "Concept map"],
  ["pdf", "PDF"],
] as const;

export default function PaperPage() {
  const { id = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "overview";
  const target = sp.get("p");
  const [paper, setPaper] = useState<PaperFull | null>(null);
  const [err, setErr] = useState("");
  const { setFocus, refresh } = useApp();

  const load = () => api.paper(id).then(setPaper).catch((e) => setErr(String(e)));
  useEffect(() => {
    setPaper(null);
    load();
    setFocus({ paper_id: id });
  }, [id]);
  // poll while analysis is running
  useEffect(() => {
    if (!paper || !["analyzing", "queued"].includes(paper.status)) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [paper?.status]);

  const goto = (pid: string) => setSp({ tab: "read", p: pid });

  if (err) return <div className="page"><p className="error">{err}</p></div>;
  if (!paper) return <div className="page"><p className="muted">Loading…</p></div>;

  const running = ["analyzing", "queued"].includes(paper.status);
  const start = async (stages?: string[]) => {
    try {
      await api.analyze(id, stages);
      await load();
      refresh();
    } catch (e) {
      alert(String(e));
    }
  };
  const lowPages = paper.page_quality.map((q, i) => [q, i + 1] as const).filter(([q]) => q < 0.8).map(([, p]) => p);

  return (
    <div className="page paper">
      <div className="paper-head">
        <div>
          <div className="eyebrow">{paper.card?.paper_type ?? "paper"} · {paper.n_pages} pages{paper.card?.field ? ` · ${paper.card.field}` : ""}</div>
          <h1>{paper.title}</h1>
          {paper.card && <p className="tldr">{paper.card.tldr}</p>}
          {lowPages.length > 0 && (
            <p className="pill warn small">Text extraction was weak on page{lowPages.length > 1 ? "s" : ""} {lowPages.join(", ")}: check those against the PDF.</p>
          )}
        </div>
        <div className="actions">
          {running ? (
            <div className="progress"><span className="spinner" /> {paper.progress || "working…"}</div>
          ) : (
            <>
              {paper.status === "error" && <p className="error small">{paper.progress}</p>}
              <button className="btn primary" onClick={() => start()}>
                {paper.analyses.length ? "Re-run full analysis" : "Analyze this paper"}
              </button>
              {!paper.analyses.includes("card") && (
                <button className="btn" onClick={() => start(["card", "graph"])} title="Card + graph only: cheaper, good for skimming">Quick card only</button>
              )}
            </>
          )}
          {paper.card?.verification && <Verification v={paper.card.verification} />}
        </div>
      </div>

      <div className="tabs">
        {TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setSp({ tab: k })}>{label}</button>
        ))}
      </div>

      {tab === "overview" && (paper.card ? <Overview card={paper.card} goto={goto} /> : <Empty what="paper card" onRun={() => start(["card", "graph"])} running={running} />)}
      {tab === "read" && <Reader paper={paper} target={target} onRun={() => start(["paragraphs"])} running={running} />}
      {tab === "threads" && (paper.threads ? <Threads threads={paper.threads.threads} paper={paper} goto={goto} /> : <Empty what="threads" onRun={() => start(paper.card ? ["threads"] : ["card", "threads"])} running={running} />)}
      {tab === "graph" && (paper.graph ? <PaperGraph g={paper.graph} goto={goto} /> : <Empty what="concept map" onRun={() => start(["graph"])} running={running} />)}
      {tab === "pdf" && <iframe className="pdf" src={pdfUrl(id, Number(sp.get("page")) || undefined)} title="PDF" />}
    </div>
  );
}

function Verification({ v }: { v: NonNullable<Card["verification"]> }) {
  const pct = v.claims ? Math.round((100 * v.grounded) / v.claims) : 0;
  return (
    <div className="verif" title="Share of card claims whose evidence quotes were found verbatim in the cited paragraph">
      <strong>{pct}%</strong> of {v.claims} claims grounded
      <div className="bar"><div style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

function Empty({ what, onRun, running }: { what: string; onRun: () => void; running: boolean }) {
  return (
    <div className="empty">
      <p>No {what} yet.</p>
      <button className="btn primary" disabled={running} onClick={onRun}>{running ? "Working…" : `Generate ${what}`}</button>
    </div>
  );
}

// ---------------------------------------------------------------- overview
function Overview({ card, goto }: { card: Card; goto: (pid: string) => void }) {
  return (
    <div className="overview">
      <section className="hero-grid">
        <Block title="Aim" tone="indigo"><p>{card.aim.text}</p><Evidence item={card.aim} onOpen={goto} /></Block>
        <Block title="Problem & motivation" tone="slate"><p>{card.problem_and_motivation.text}</p><Evidence item={card.problem_and_motivation} onOpen={goto} /></Block>
      </section>

      {card.research_questions.length > 0 && (
        <Block title="Research questions">
          {card.research_questions.map((q) => <Row key={q.id} tag={q.id} item={q} goto={goto}>{q.text}</Row>)}
        </Block>
      )}
      <Block title="Hypotheses">
        {card.hypotheses.map((h) => (
          <Row key={h.id} tag={h.id} item={h} goto={goto}>
            {h.text} {!h.explicit && <span className="pill small" title="Not stated as a hypothesis; inferred from what the paper tests">inferred</span>}
          </Row>
        ))}
      </Block>

      <Block title="Methodology">
        <p className="lead">{card.methodology.overview}</p>
        <div className="components">
          {card.methodology.components.map((m) => (
            <div key={m.id} className="comp">
              <div className="comp-h"><span className="tag">{m.id}</span> {m.name} <GroundBadge item={m} /></div>
              <p>{m.description}</p>
              <Evidence item={m} onOpen={goto} />
            </div>
          ))}
        </div>
      </Block>

      <Block title="How they ran the experiments">
        <div className="table-wrap">
          <table>
            <thead><tr><th></th><th>Experiment</th><th>Tests</th><th>Data</th><th>Models / baselines</th><th>Metrics</th><th>Setup</th></tr></thead>
            <tbody>
              {card.experiments.map((e) => (
                <tr key={e.id}>
                  <td><span className="tag">{e.id}</span></td>
                  <td><strong>{e.name}</strong><div className="muted small">{e.purpose}</div><Evidence item={e} onOpen={goto} /></td>
                  <td>{e.tests.map((t) => <span key={t} className="tag ghost">{t}</span>)}</td>
                  <td>{e.datasets.join(", ")}</td>
                  <td>{e.models.join(", ")}{e.baselines.length ? <div className="muted small">vs. {e.baselines.join(", ")}</div> : null}</td>
                  <td>{e.metrics.join(", ")}</td>
                  <td className="small">{e.setup}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Block>

      <Block title="Results and how they're reported">
        <div className="results">
          {card.results.map((r) => (
            <div key={r.id} className={`result str-${r.strength}`}>
              <div className="result-h">
                <span className="tag">{r.id}</span>
                {r.supports.map((s) => <span key={s} className="tag ghost">→ {s}</span>)}
                <span className={`pill small str-${r.strength}`}>{r.strength} evidence</span>
                <span className="pill small">{r.statistical_rigor}</span>
                <GroundBadge item={r} />
              </div>
              <p className="claim">{r.claim}</p>
              {r.key_numbers && <p className="nums">{r.key_numbers}</p>}
              <p className="muted small">Reported via: {r.how_reported}</p>
              <Evidence item={r} onOpen={goto} />
            </div>
          ))}
        </div>
      </Block>

      <section className="two">
        <Block title="Limitations the authors state" tone="amber">
          {card.limitations_stated.length ? card.limitations_stated.map((l, i) => <Row key={i} item={l} goto={goto}>{l.text}</Row>) : <p className="muted">None stated.</p>}
        </Block>
        <Block title="Limitations a committee would raise" tone="red">
          {card.limitations_inferred.map((l, i) => (
            <Row key={i} item={l} goto={goto}>{l.text}<div className="muted small">{l.reasoning}</div></Row>
          ))}
        </Block>
      </section>

      <section className="two">
        <Block title="Contributions">{card.contributions.map((c, i) => <Row key={i} item={c} goto={goto}>{c.text}</Row>)}</Block>
        <Block title="Future work">{card.future_work.map((c, i) => <Row key={i} item={c} goto={goto}>{c.text}</Row>)}</Block>
      </section>

      <Block title="Key terms">
        <dl className="terms">
          {card.key_terms.map((t) => (
            <div key={t.term}><dt>{t.term}</dt><dd>{t.definition} <Evidence item={t} onOpen={goto} /></dd></div>
          ))}
        </dl>
      </Block>
    </div>
  );
}

function Block({ title, children, tone }: { title: string; children: React.ReactNode; tone?: string }) {
  return <section className={`block ${tone ? "tone-" + tone : ""}`}><h3>{title}</h3>{children}</section>;
}
function Row({ tag, item, goto, children }: { tag?: string; item: any; goto: (p: string) => void; children: React.ReactNode }) {
  return (
    <div className={`row ${item.grounded === false ? "ungrounded" : ""}`}>
      {tag && <span className="tag">{tag}</span>}
      <div className="row-body">{children} <GroundBadge item={item} /><div><Evidence item={item} onOpen={goto} /></div></div>
    </div>
  );
}

// ------------------------------------------------------------------ reader
const ROLE_COLORS: Record<string, string> = {
  motivation: "#0ea5e9", background: "#64748b", "related-work": "#94a3b8", definition: "#0891b2", claim: "#4f46e5",
  hypothesis: "#7c3aed", method: "#9333ea", "experimental-setup": "#c026d3", result: "#059669", analysis: "#16a34a",
  limitation: "#dc2626", "future-work": "#d97706", "figure/table": "#78716c", other: "#a8a29e",
};

function Reader({ paper, target, onRun, running }: { paper: PaperFull; target: string | null; onRun: () => void; running: boolean }) {
  const { setFocus } = useApp();
  const ex = useMemo(() => Object.fromEntries((paper.explanations?.paragraphs ?? []).map((e) => [e.pid, e])), [paper.explanations]);
  const [roleFilter, setRoleFilter] = useState<string>("");
  const refs = useRef<Record<string, HTMLDivElement | null>>({});
  const [active, setActive] = useState<string | null>(target);

  useEffect(() => {
    if (target && refs.current[target]) {
      refs.current[target]!.scrollIntoView({ behavior: "smooth", block: "center" });
      setActive(target);
    }
  }, [target, paper.paragraphs.length]);

  const visible = paper.paragraphs.filter((p) => !["reference", "figure_text"].includes(p.kind));
  const nExplained = Object.keys(ex).length;
  const roles = Array.from(new Set(Object.values(ex).map((e) => e.role)));

  return (
    <div className="reader">
      <div className="reader-bar">
        <span className="muted">{nExplained ? `${nExplained} paragraphs explained` : "Paragraph explanations not generated yet."}</span>
        {!nExplained && <button className="btn primary small" disabled={running} onClick={onRun}>{running ? "Working…" : "Explain every paragraph"}</button>}
        {roles.length > 0 && (
          <div className="role-filter">
            <button className={!roleFilter ? "on" : ""} onClick={() => setRoleFilter("")}>all</button>
            {roles.map((r) => (
              <button key={r} className={roleFilter === r ? "on" : ""} style={{ ["--c" as any]: ROLE_COLORS[r] }} onClick={() => setRoleFilter(r)}>{r}</button>
            ))}
          </div>
        )}
      </div>
      {visible.map((p) => {
        const e: Explanation | undefined = ex[p.pid];
        if (roleFilter && (!e || e.role !== roleFilter) && p.kind !== "heading") return null;
        if (p.kind === "heading") return <h2 key={p.pid} className="sec-h" id={p.pid}>{p.text}</h2>;
        if (p.kind === "front") return <div key={p.pid} className="front-line">{p.text}</div>;
        return (
          <div
            key={p.pid}
            ref={(el) => (refs.current[p.pid] = el)}
            className={`para ${active === p.pid ? "active" : ""} ${p.kind}`}
            onClick={() => { setActive(p.pid); setFocus({ paper_id: paper.id, pid: p.pid }); }}
          >
            <div className="orig">
              <div className="meta"><a href={`?tab=pdf&page=${p.page}`}>{p.pid} · p.{p.page}</a>{e && <span className="role" style={{ background: ROLE_COLORS[e.role] }}>{e.role}</span>}</div>
              <p>{p.text}</p>
            </div>
            <div className="expl">
              {e ? (
                <>
                  <p>{e.explanation}</p>
                  {e.why_it_matters && <p className="why"><strong>Why it matters:</strong> {e.why_it_matters}</p>}
                  <div className="chips">
                    {e.links.map((l) => <span key={l} className="tag ghost">{l}</span>)}
                    {e.depends_on.map((d) => <a key={d} className="tag dep" href={`?tab=read&p=${d}`} onClick={(ev) => ev.stopPropagation()}>needs {d}</a>)}
                    {e.key_terms.map((t) => <span key={t} className="term">{t}</span>)}
                    <span className="diff" title="difficulty">{"●".repeat(e.difficulty)}{"○".repeat(5 - e.difficulty)}</span>
                  </div>
                </>
              ) : (
                <p className="muted small">—</p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ----------------------------------------------------------------- threads
const STAGES = ["question", "hypothesis", "definition", "method", "experiment", "result", "interpretation", "limitation"];
function Threads({ threads, paper, goto }: { threads: Thread[]; paper: PaperFull; goto: (pid: string) => void }) {
  const text = useMemo(() => Object.fromEntries(paper.paragraphs.map((p: Paragraph) => [p.pid, p.text])), [paper]);
  return (
    <div className="threads">
      {threads.map((t) => (
        <section key={t.id} className="thread">
          <div className="thread-h">
            <span className="pill small">{t.kind}</span>
            <h3>{t.title}</h3>
          </div>
          <p className="muted">{t.summary}</p>
          <ol className="chain">
            {t.steps.map((s, i) => (
              <li key={i} className={`stage-${STAGES.includes(s.stage) ? s.stage : "other"}`}>
                <div className="stage">{s.stage}{s.card_ref ? ` · ${s.card_ref}` : ""}</div>
                <div className="step-body">
                  <p>{s.note}</p>
                  <blockquote onClick={() => goto(s.pid)} title="Open in reader">
                    <span className="tag ghost">{s.pid}</span> {(text[s.pid] ?? "").slice(0, 260)}{(text[s.pid] ?? "").length > 260 ? "…" : ""}
                  </blockquote>
                </div>
              </li>
            ))}
          </ol>
          <div className="verdict"><strong>Verdict:</strong> {t.verdict}</div>
          {t.gaps.length > 0 && <ul className="gaps">{t.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul>}
        </section>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------- graph
const TYPE_COLORS: Record<string, string> = {
  method: "#7c3aed", dataset: "#059669", benchmark: "#059669", metric: "#d97706", model: "#db2777", concept: "#0891b2",
  task: "#65a30d", "bias-type": "#dc2626", finding: "#4f46e5", limitation: "#b91c1c",
};
function PaperGraph({ g, goto }: { g: NonNullable<PaperFull["graph"]>; goto: (pid: string) => void }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(900);
  const [sel, setSel] = useState<any>(null);
  useEffect(() => { if (wrap.current) setW(wrap.current.clientWidth); }, []);
  const fg = useSpreadGraph([g], -220, 70, 34);
  const data = useMemo(() => {
    const ids = new Set(g.entities.map((e: any) => e.name));
    const links = g.relations.filter((r: any) => ids.has(r.src) && ids.has(r.dst)).map((r: any) => ({ source: r.src, target: r.dst, rel: r.rel }));
    // hub: this paper, linked to every entity so the map is one connected picture
    const hub = { id: "__paper__", name: "this paper", type: "paper", description: "", evidence: [] };
    const hubLinks = g.entities.map((e: any) => ({ source: "__paper__", target: e.name, rel: "", hub: true }));
    return { nodes: [hub, ...g.entities.map((e: any) => ({ id: e.name, ...e }))], links: [...hubLinks, ...links] };
  }, [g]);
  return (
    <div className="graph-wrap" ref={wrap}>
      <ForceGraph2D
        ref={fg.ref}
        onEngineStop={fg.onEngineStop}
        cooldownTicks={150}
        graphData={data}
        width={w}
        height={560}
        nodeRelSize={5}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={1}
        linkColor={(l: any) => (l.hub ? "rgba(37,99,235,.08)" : "rgba(100,116,139,.45)")}
        linkDirectionalArrowColor={() => "rgba(100,116,139,.6)"}
        onNodeClick={(n: any) => setSel(n)}
        nodeCanvasObject={(n: any, ctx, scale) => {
          const r = n.type === "paper" ? 8 : 5;
          ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI); ctx.fillStyle = n.type === "paper" ? "#2563eb" : TYPE_COLORS[n.type] ?? "#64748b"; ctx.fill();
          const fs = 11 / scale; ctx.font = `${fs}px Inter, system-ui`; ctx.fillStyle = "#1e293b"; ctx.textAlign = "center";
          ctx.fillText(n.name, n.x, n.y + r + fs);
        }}
        linkCanvasObjectMode={() => "after"}
        linkCanvasObject={(l: any, ctx, scale) => {
          if (scale < 1.6 || l.hub) return;
          const x = (l.source.x + l.target.x) / 2, y = (l.source.y + l.target.y) / 2;
          ctx.font = `${8 / scale}px Inter, system-ui`; ctx.fillStyle = "#64748b"; ctx.textAlign = "center"; ctx.fillText(l.rel, x, y);
        }}
      />
      <Legend />
      {sel && (
        <div className="node-card">
          <button className="x" onClick={() => setSel(null)}>×</button>
          <div className="eyebrow">{sel.type}</div>
          <h4>{sel.name}</h4>
          <p>{sel.description}</p>
          <Evidence item={sel} onOpen={goto} />
        </div>
      )}
      {g.paper_links?.length > 0 && (
        <div className="links-list">
          <h3>Links to other papers in your library</h3>
          {g.paper_links.map((l: any, i: number) => (
            <div key={i} className="row"><span className="pill small">{l.rel}</span><div className="row-body"><strong>{l.other_title}</strong><div className="muted small">{l.explanation}</div><Evidence item={l} onOpen={goto} /></div></div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Legend() {
  return (
    <div className="legend">
      {Object.entries({ paper: "#2563eb", ...TYPE_COLORS }).filter(([k]) => k !== "benchmark").map(([k, c]) => (
        <span key={k}><i style={{ background: c }} />{k}</span>
      ))}
    </div>
  );
}
