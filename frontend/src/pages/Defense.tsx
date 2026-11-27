import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "../api";
import { useApp } from "../App";
import { linkCitations } from "../components/Evidence";

type Round = { q: any; answer?: string; grade?: any };

export default function Defense() {
  const { papers, openCitation } = useApp();
  const [picked, setPicked] = useState<string[]>([]);
  const [focus, setFocus] = useState("");
  const [difficulty, setDifficulty] = useState(3);
  const [rounds, setRounds] = useState<Round[]>([]);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState("");
  const [filter, setFilter] = useState("");

  const cur = rounds[rounds.length - 1];
  const ask = async () => {
    setBusy("q");
    try {
      const q = await api.defenseQ(picked, rounds.map((r) => r.q.question), focus, difficulty);
      setRounds((r) => [...r, { q }]);
      setAnswer("");
    } catch (e) { alert(String(e)); } finally { setBusy(""); }
  };
  const grade = async () => {
    setBusy("g");
    try {
      const g = await api.defenseG(picked, cur.q, answer);
      setRounds((r) => r.map((x, i) => (i === r.length - 1 ? { ...x, answer, grade: g } : x)));
    } catch (e) { alert(String(e)); } finally { setBusy(""); }
  };
  const avg = rounds.filter((r) => r.grade).reduce((a, r, _, arr) => a + r.grade.score / arr.length, 0);

  return (
    <div className="page defense">
      <h1>Defense prep</h1>
      <p className="lead muted">A committee member who has read your papers asks one question at a time, grades your answer against the text, and asks the follow-up.</p>

      <div className="defense-grid">
        <div className="block">
          <h3>1. Papers in scope ({picked.length})</h3>
          <input className="search" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <ul className="pick">
            {papers.filter((p) => p.title.toLowerCase().includes(filter.toLowerCase())).map((p) => (
              <li key={p.id}>
                <label>
                  <input type="checkbox" checked={picked.includes(p.id)} onChange={(e) => setPicked((s) => (e.target.checked ? [...s, p.id] : s.filter((x) => x !== p.id)))} />
                  {p.title} {p.analyses.includes("card") ? "" : <span className="muted small">(no card)</span>}
                </label>
              </li>
            ))}
          </ul>
          <h3>2. Focus</h3>
          <input className="search" placeholder="e.g. statistical rigour, choice of bias metrics, generalisation" value={focus} onChange={(e) => setFocus(e.target.value)} />
          <label className="diff-pick">Difficulty <input type="range" min={1} max={5} value={difficulty} onChange={(e) => setDifficulty(Number(e.target.value))} /> {difficulty}/5</label>
          <button className="btn primary" disabled={!picked.length || !!busy} onClick={ask}>{busy === "q" ? "Thinking…" : rounds.length ? "Next question" : "Start examination"}</button>
          {rounds.some((r) => r.grade) && <p className="score-avg">Average so far: <strong>{avg.toFixed(1)}/10</strong> over {rounds.filter((r) => r.grade).length} answers</p>}
        </div>

        <div className="exam">
          {!cur && <div className="empty"><p>Pick one or more papers (your own, or the ones your committee will expect you to know) and start.</p></div>}
          {cur && (
            <>
              <div className="question">
                <div className="eyebrow">{cur.q.category} · difficulty {cur.q.difficulty}/5 · question {rounds.length}</div>
                <h2>{cur.q.question}</h2>
              </div>
              {!cur.grade && (
                <>
                  <textarea className="answer" rows={9} placeholder="Answer as you would in the room. Be specific." value={answer} onChange={(e) => setAnswer(e.target.value)} />
                  <button className="btn primary" disabled={!answer.trim() || !!busy} onClick={grade}>{busy === "g" ? "Grading…" : "Submit answer"}</button>
                </>
              )}
              {cur.grade && (
                <div className="grade">
                  <div className={`score s${Math.round(cur.grade.score / 2.5)}`}>{cur.grade.score}<span>/10</span></div>
                  <p>{cur.grade.feedback}</p>
                  <div className="two">
                    <div><h4>Covered</h4><ul>{cur.grade.covered.map((x: string, i: number) => <li key={i} className="ok">{x}</li>)}</ul></div>
                    <div><h4>Missed</h4><ul>{cur.grade.missed.map((x: string, i: number) => <li key={i} className="bad">{x}</li>)}</ul></div>
                  </div>
                  {cur.grade.errors?.length > 0 && <div className="errors"><h4>Contradicts the papers</h4><ul>{cur.grade.errors.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>}
                  <details open>
                    <summary>Model answer</summary>
                    <div className="md">
                      <ReactMarkdown components={{
                        a: ({ href, children }) => {
                          const c = href?.match(/^#cite\/([0-9a-f]{12})\/(p\d+)$/);
                          return c ? <button className="cite" onClick={() => openCitation(c[1], c[2])}>{children}</button> : <a href={href}>{children}</a>;
                        },
                      }}>{linkCitations(cur.grade.model_answer)}</ReactMarkdown>
                    </div>
                  </details>
                  <p className="follow"><strong>Follow-up they'd ask:</strong> {cur.grade.follow_up}</p>
                  <button className="btn" onClick={() => { setRounds((r) => [...r, { q: { ...cur.q, question: cur.grade.follow_up, category: "follow-up", rubric: [] } }]); setAnswer(""); }}>Answer the follow-up</button>
                </div>
              )}
              {rounds.length > 1 && (
                <details className="history">
                  <summary>Earlier questions</summary>
                  {rounds.slice(0, -1).map((r, i) => <p key={i}><strong>{r.grade?.score ?? "–"}/10</strong> {r.q.question}</p>)}
                </details>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
