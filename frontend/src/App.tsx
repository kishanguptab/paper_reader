import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, DEMO, PaperSummary } from "./api";
import Library from "./pages/Library";
import PaperPage from "./pages/Paper";
import GraphPage from "./pages/GraphPage";
import Defense from "./pages/Defense";
import Chat from "./components/Chat";

type Ctx = {
  papers: PaperSummary[];
  refresh: () => Promise<void>;
  focus: { paper_id?: string; pid?: string };
  setFocus: (f: { paper_id?: string; pid?: string }) => void;
  openCitation: (paperId: string, pid: string) => void;
  titleOf: (id: string) => string;
  status: { has_key: boolean; spent: number; budget: number } | null;
};
const AppCtx = createContext<Ctx>(null as any);
export const useApp = () => useContext(AppCtx);

export default function App() {
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [focus, setFocus] = useState<{ paper_id?: string; pid?: string }>({});
  const [status, setStatus] = useState<Ctx["status"]>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const nav = useNavigate();

  const refresh = useCallback(async () => {
    const [p, s] = await Promise.all([api.papers(), api.status()]);
    setPapers(p);
    setStatus(s);
  }, []);

  useEffect(() => {
    refresh().catch(() => {});
    if (DEMO) return;
    const t = setInterval(() => refresh().catch(() => {}), 4000);
    return () => clearInterval(t);
  }, [refresh]);

  const openCitation = useCallback((paperId: string, pid: string) => {
    nav(`/paper/${paperId}?tab=read&p=${pid}`);
  }, [nav]);

  const titles = useMemo(() => Object.fromEntries(papers.map((p) => [p.id, p.title])), [papers]);
  const ctx: Ctx = { papers, refresh, focus, setFocus, openCitation, titleOf: (id) => titles[id] ?? id, status };
  const shown = papers.filter((p) => p.title.toLowerCase().includes(filter.toLowerCase()));

  return (
    <AppCtx.Provider value={ctx}>
      <div className={`shell ${chatOpen ? "chat-open" : ""}`}>
        <header className="topbar">
          <NavLink to="/" className="brand">
            <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden><circle cx="13" cy="13" r="9" fill="none" stroke="currentColor" strokeWidth="3" /><line x1="20" y1="20" x2="28" y2="28" stroke="currentColor" strokeWidth="3" strokeLinecap="round" /></svg>
            PaperLens
          </NavLink>
          <nav>
            <NavLink to="/" end>Library</NavLink>
            <NavLink to="/graph">Knowledge graph</NavLink>
            <NavLink to="/defense">Defense prep</NavLink>
          </nav>
          <div className="spacer" />
          {DEMO && <a className="pill" href="https://github.com/kishanguptab/paper_reader" target="_blank" rel="noreferrer">Live demo · code on GitHub ↗</a>}
          {status && !DEMO && (
            <div className="meter" title="Claude API spend vs. budget cap (set PAPERLENS_BUDGET_USD)">
              <span>${status.spent.toFixed(2)}</span>
              <div className="bar"><div style={{ width: `${Math.min(100, (100 * status.spent) / status.budget)}%` }} /></div>
              <span className="muted">${status.budget.toFixed(0)}</span>
            </div>
          )}
          {status && !status.has_key && <span className="pill warn">No API key</span>}
          <button className="btn primary" onClick={() => setChatOpen((o) => !o)}>{chatOpen ? "Close chat" : "Ask your papers"}</button>
        </header>

        <aside className="sidebar">
          <input className="search" placeholder={`Filter ${papers.length} papers…`} value={filter} onChange={(e) => setFilter(e.target.value)} />
          <ul className="paper-list">
            {shown.map((p) => (
              <li key={p.id}>
                <NavLink to={`/paper/${p.id}`} className={({ isActive }) => (isActive ? "active" : "")}>
                  <span className={`dot s-${p.status}`} title={p.status} />
                  <span className="t">{p.title}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </aside>

        <main className="main">
          <Routes>
            <Route path="/" element={<Library />} />
            <Route path="/paper/:id" element={<PaperPage />} />
            <Route path="/graph" element={<GraphPage />} />
            <Route path="/defense" element={<Defense />} />
          </Routes>
        </main>

        {chatOpen && (
          <aside className="chat-drawer">
            <Chat />
          </aside>
        )}
      </div>
    </AppCtx.Provider>
  );
}
