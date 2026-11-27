import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, ChatResult, DEMO } from "../api";
import { getSample } from "../demo";
import { useApp } from "../App";
import { linkCitations } from "./Evidence";

type Msg = { role: "user" | "assistant"; content: string; meta?: ChatResult };

const STARTERS = DEMO
  ? [
      "Do intrinsic bias metrics predict downstream bias? What's the evidence?",
      "What problems do the papers find with StereoSet and CrowS-Pairs as benchmarks?",
      "Compare how StereoSet, CrowS-Pairs and BBQ measure bias.",
      "Did any of these papers test bias in quantized Llama 4 models?",
    ]
  : [
      "Compare how the papers measure bias in compressed models. Which metrics do they share?",
      "Which papers find that compression amplifies bias, and which find it reduces bias?",
      "What are the most common limitations across the quantization papers?",
      "Do intrinsic bias metrics predict downstream bias? What's the evidence?",
    ];

export default function Chat() {
  const { focus, openCitation, titleOf, setFocus } = useApp();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const [live, setLive] = useState<boolean | null>(DEMO ? null : true);
  useEffect(() => {
    if (DEMO) getSample().then((s) => setLive(!!s));
  }, []);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" }); // braces: newer browsers return a Promise here, which React would treat as cleanup
  }, [msgs, busy]);

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setQ("");
    setBusy(true);
    try {
      const r = await api.chat(text, history, focus);
      setHistory(r.history.slice(-12));
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, meta: r }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: `**Error:** ${String(e)}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="chat">
      <div className="chat-h">
        <h3>Ask your papers</h3>
        {focus.paper_id && (
          <div className="focus">
            Focused on <strong title={titleOf(focus.paper_id)}>{titleOf(focus.paper_id).slice(0, 38)}…</strong>
            {focus.pid && <span className="tag ghost">{focus.pid}</span>}
            <button className="x" onClick={() => setFocus({})} title="Clear focus">×</button>
          </div>
        )}
      </div>
      <div className="chat-body">
        {live === false && (
          <div className="demo-note">
            Chat runs on the viewer's own Claude session, and none is available in this view. Open this page on claude.ai while signed in to
            ask questions; everything else on the page works without it.
          </div>
        )}
        {msgs.length === 0 && live !== false && (
          <div className="starters">
            <p className="muted small">Answers come only from your library, with every sentence cited to a paragraph. Claims that can't be grounded are flagged.</p>
            {STARTERS.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.role === "assistant" ? (
              <>
                <div className="md">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ href, children }) => {
                        const c = href?.match(/^#cite\/([0-9a-f]{12})\/(p\d+)$/);
                        if (c) {
                          const src = m.meta?.sources.find((s) => s.paper_id === c[1] && s.pid === c[2]);
                          return (
                            <button className="cite" title={`${titleOf(c[1])}\n\n${src?.text.slice(0, 300) ?? ""}`} onClick={() => openCitation(c[1], c[2])}>
                              {children}
                            </button>
                          );
                        }
                        return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                      },
                    }}
                  >
                    {linkCitations(m.content)}
                  </ReactMarkdown>
                </div>
                {m.meta && <Honesty meta={m.meta} />}
              </>
            ) : (
              <p>{m.content}</p>
            )}
          </div>
        ))}
        {busy && <div className="msg assistant"><span className="spinner" /> searching and reading…</div>}
        <div ref={end} />
      </div>
      <form className="chat-in" onSubmit={(e) => { e.preventDefault(); send(q); }}>
        <textarea value={q} placeholder="Ask about a paper, compare papers, or check a claim…" onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(q); } }} rows={2} />
        <button className="btn primary" disabled={busy || !q.trim() || live === false}>Send</button>
      </form>
    </div>
  );
}

function Honesty({ meta }: { meta: ChatResult }) {
  const c = meta.check;
  const papers = new Set(meta.sources.map((s) => s.paper_id)).size;
  return (
    <div className="honesty">
      <span className={c.ok ? "ok" : "bad"}>{c.ok ? "✓" : "⚠"} {c.citations} citations{c.invalid_citations.length ? `, ${c.invalid_citations.length} invalid` : ""}</span>
      <span>{papers} paper{papers === 1 ? "" : "s"}</span>
      {c.uncited_sentences > 0 && <span className="warn">{c.uncited_sentences}/{c.sentences} sentences uncited</span>}
      {c.repaired && <span title="The first draft cited paragraphs it hadn't read; it was asked to fix them">self-corrected</span>}
      <details>
        <summary>{meta.trace.length} tool calls</summary>
        <ol>{meta.trace.map((t, i) => <li key={i}><code>{t.tool}</code> {JSON.stringify(t.input).slice(0, 90)} → {t.n}</li>)}</ol>
      </details>
    </div>
  );
}
