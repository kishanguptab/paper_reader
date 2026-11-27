export type Evidence = { pid: string; quote: string; score?: number; verified?: boolean };
export type Grounded = { evidence?: Evidence[]; grounded?: boolean };

export type Paragraph = {
  pid: string; idx: number; page: number; bbox: number[]; kind: string; section: string; text: string;
};

export type PaperSummary = {
  id: string; title: string; n_pages: number; status: string; progress: string;
  analyses: string[]; page_quality: number[]; path: string;
};

export type Card = {
  tldr: string; paper_type: string; field: string;
  aim: Grounded & { text: string };
  problem_and_motivation: Grounded & { text: string };
  research_questions: (Grounded & { id: string; text: string })[];
  hypotheses: (Grounded & { id: string; text: string; explicit: boolean })[];
  contributions: (Grounded & { text: string })[];
  methodology: { overview: string; components: (Grounded & { id: string; name: string; description: string })[] };
  experiments: (Grounded & {
    id: string; name: string; purpose: string; tests: string[]; datasets: string[]; models: string[];
    baselines: string[]; metrics: string[]; setup: string;
  })[];
  results: (Grounded & {
    id: string; claim: string; from_experiment?: string; supports: string[]; how_reported: string;
    key_numbers: string; statistical_rigor: string; strength: string;
  })[];
  limitations_stated: (Grounded & { text: string })[];
  limitations_inferred: (Grounded & { text: string; reasoning: string })[];
  future_work: (Grounded & { text: string })[];
  key_terms: (Grounded & { term: string; definition: string })[];
  verification?: { claims: number; grounded: number; quotes: number; quotes_ok: number };
};

export type Explanation = {
  pid: string; explanation: string; role: string; why_it_matters: string; key_terms: string[];
  depends_on: string[]; links: string[]; difficulty: number;
};

export type Thread = {
  id: string; title: string; kind: string; summary: string;
  steps: { pid: string; stage: string; note: string; card_ref?: string }[];
  verdict: string; gaps: string[];
};

export type PaperFull = PaperSummary & {
  paragraphs: Paragraph[];
  card: Card | null;
  explanations: { paragraphs: Explanation[] } | null;
  threads: { threads: Thread[] } | null;
  graph: { entities: any[]; relations: any[]; paper_links: any[] } | null;
};

export type GraphData = {
  nodes: { id: string; label: string; type: string; papers: string[]; description: string; color: string; degree: number }[];
  edges: { src: string; dst: string; rel: string; paper_id: string; grounded?: boolean; explanation?: string }[];
  review: { a: string; b: string; a_label: string; b_label: string; score: number }[];
  shared: { a: string; b: string; entities: string[] }[];
};

export type ChatResult = {
  answer: string;
  check: { citations: number; invalid_citations: string[]; uncited_sentences: number; sentences: number; ok: boolean; repaired?: boolean };
  trace: { tool: string; input: any; n: number }[];
  sources: { paper_id: string; pid: string; title: string; text: string }[];
  history: { role: string; content: string }[];
};

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}
const post = (url: string, body: unknown) =>
  fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const liveApi = {
  status: () => fetch("/api/status").then(j<{ has_key: boolean; spent: number; budget: number; papers_dir: string; models: Record<string, string> }>),
  papers: () => fetch("/api/papers").then(j<PaperSummary[]>),
  paper: (id: string) => fetch(`/api/papers/${id}`).then(j<PaperFull>),
  analyze: (id: string, stages?: string[]) => post(`/api/papers/${id}/analyze`, stages ? { stages } : {}).then(j<{ started: boolean }>),
  analyzeAll: (stages: string[]) => post("/api/analyze-all", { stages }).then(j<{ queued: number }>),
  rescan: () => post("/api/rescan", {}).then(j<{ added_or_updated: number }>),
  upload: (f: File) => { const fd = new FormData(); fd.append("file", f); return fetch("/api/upload", { method: "POST", body: fd }).then(j<{ paper_id: string }>); },
  graph: () => fetch("/api/graph").then(j<GraphData>),
  chat: (question: string, history: any[], focus?: { paper_id?: string; pid?: string }) => post("/api/chat", { question, history, focus }).then(j<ChatResult>),
  cost: () => fetch("/api/cost").then(j<{ total: number; by_purpose: any[] }>),
  defenseQ: (paper_ids: string[], asked: string[], focus: string, difficulty: number) => post("/api/defense/question", { paper_ids, asked, focus, difficulty }).then(j<any>),
  defenseG: (paper_ids: string[], question: any, answer: string) => post("/api/defense/grade", { paper_ids, question, answer }).then(j<any>),
};

import { DEMO, demoData, demoSummaries, demoChat, demoQuestion, demoGrade } from "./demo";

const off = () => Promise.reject(new Error("Not available in the live demo: clone the repo to run analyses on your own papers."));
const demoApi: typeof liveApi = {
  status: async () => ({ has_key: true, spent: 0, budget: 0, papers_dir: "", models: {} }),
  papers: demoSummaries,
  paper: async (id) => {
    const p = (await demoData()).papers.find((x) => x.id === id);
    if (!p) throw new Error("Paper not found");
    return p;
  },
  analyze: off as any,
  analyzeAll: off as any,
  rescan: off as any,
  upload: off as any,
  graph: async () => (await demoData()).graph,
  chat: (q, h, f) => demoChat(q, h, f ?? {}),
  cost: async () => ({ total: 0, by_purpose: [] }),
  defenseQ: demoQuestion,
  defenseG: demoGrade,
};

export const api = DEMO ? demoApi : liveApi;
export { DEMO };

export const pdfUrl = (id: string, page?: number) => `/api/papers/${id}/pdf${page ? `#page=${page}` : ""}`;
