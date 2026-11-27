import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, DEMO } from "../api";
import { useApp } from "../App";

export default function Library() {
  const { papers, refresh, status } = useApp();
  const [busy, setBusy] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const carded = papers.filter((p) => p.analyses.includes("card")).length;
  const deep = papers.filter((p) => p.analyses.includes("paragraphs")).length;
  const running = papers.filter((p) => ["queued", "analyzing"].includes(p.status)).length;

  const act = async (label: string, fn: () => Promise<any>) => {
    setBusy(label);
    try { await fn(); await refresh(); } catch (e) { alert(String(e)); } finally { setBusy(""); }
  };

  return (
    <div className="page library">
      <section className="hero">
        <h1>Understand every paper, <em>paragraph by paragraph.</em></h1>
        <p className="lead">
          PaperLens reads your PDFs and builds a grounded explanation of each one: aim, hypotheses, methodology, experiments,
          how the results are reported, and limitations. Every claim links to the sentence it came from and is checked against it.
          Papers are linked through a shared knowledge graph, and a committee examiner helps you prepare your defense.
        </p>
        {DEMO && (
          <div className="demo-note">
            <strong>Live demo.</strong> {papers.length} open-access papers on bias in language models (ACL Anthology, CC BY 4.0; text shown with
            attribution), analysed by PaperLens. Three have full deep reads: <em>StereoSet</em>, <em>CrowS-Pairs</em> and <em>Intrinsic Bias
            Metrics Do Not Correlate with Application Bias</em>. <strong>Ask your papers</strong> and <strong>Defense prep</strong> run live on your own
            Claude session, with the agent's search and read tools running in this page. Analyses were generated ahead of time; the full app,
            evals and the "what broke" log are on <a href="https://github.com/kishanguptab/paper_reader" target="_blank" rel="noreferrer">GitHub</a>.
          </div>
        )}
        <div className="stats">
          <div><strong>{papers.length}</strong><span>papers</span></div>
          <div><strong>{carded}</strong><span>with paper cards</span></div>
          <div><strong>{deep}</strong><span>read in depth</span></div>
          {!DEMO && <div><strong>${status?.spent.toFixed(2) ?? "0.00"}</strong><span>API spend</span></div>}
          {DEMO && <div><strong>96.5%</strong><span>claims grounded (55-paper eval)</span></div>}
        </div>
        {!DEMO && <div className="hero-actions">
          <button className="btn primary" disabled={!!busy || !status?.has_key}
            title="Builds the card + knowledge-graph entries for every paper (cheaper than a full deep read)"
            onClick={() => act("cards", () => api.analyzeAll(["card", "graph"]))}>
            {busy === "cards" ? "Queuing…" : "Build cards + graph for all papers"}
          </button>
          <button className="btn" disabled={!!busy} onClick={() => file.current?.click()}>Add a PDF</button>
          <button className="btn ghost" disabled={!!busy} onClick={() => act("scan", api.rescan)}>Rescan folder</button>
          <input ref={file} type="file" accept="application/pdf" hidden onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) act("upload", () => api.upload(f));
          }} />
        </div>}
        {running > 0 && <p className="muted"><span className="spinner" /> {running} paper(s) in the analysis queue…</p>}
        {status && !status.has_key && (
          <p className="pill warn">Add ANTHROPIC_API_KEY to the .env file and restart the backend to enable analysis and chat.</p>
        )}
      </section>

      <div className="grid">
        {papers.map((p) => (
          <Link key={p.id} to={`/paper/${p.id}`} className="tile">
            <div className="tile-top">
              <span className={`dot s-${p.status}`} />
              <span className="muted small">{p.n_pages} pages</span>
            </div>
            <h3>{p.title}</h3>
            <div className="chips">
              {["card", "graph", "paragraphs", "threads"].map((k) => (
                <span key={k} className={`chip ${p.analyses.includes(k) ? "on" : ""}`}>{k}</span>
              ))}
            </div>
            {["queued", "analyzing"].includes(p.status) && <div className="muted small"><span className="spinner" /> {p.progress}</div>}
            {p.status === "error" && <div className="error small">{p.progress.slice(0, 120)}</div>}
          </Link>
        ))}
      </div>
    </div>
  );
}
