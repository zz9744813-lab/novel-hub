const BASE = "";

async function fetchJSON<T>(url: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(BASE + url, {
    headers: { "Content-Type": "application/json", ...opts?.headers },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export const api = {
  books: {
    list: () => fetchJSON<Book[]>("/api/books"),
    create: (data: { title: string; description?: string; target_chapters?: number }) =>
      fetchJSON<{ book_id: string }>("/api/books", { method: "POST", body: JSON.stringify(data) }),
    get: (id: string) => fetchJSON<Book>(`/api/books/${id}`),
  },
  outlines: {
    parse: (bookId: string, data: { raw_outline: string; target_chapter_count?: number }) =>
      fetchJSON<{ outline_version_id: string; status: string; errors?: string[] }>(
        `/api/books/${bookId}/outlines/parse`, { method: "POST", body: JSON.stringify(data) }
      ),
    graph: (bookId: string) => fetchJSON<{ nodes: OutlineNode[] }>(`/api/books/${bookId}/outline-graph`),
    approve: (bookId: string, version: number) =>
      fetchJSON<{ status: string }>(`/api/books/${bookId}/outlines/${version}/approve`, { method: "POST" }),
  },
  chapters: {
    run: (bookId: string, chapterNo: number) =>
      fetchJSON<{ chapter_id: string; status: string }>(`/api/books/${bookId}/chapters/${chapterNo}/run`, { method: "POST" }),
    get: (id: string) => fetchJSON<Chapter>(`/api/chapters/${id}`),
    pause: (id: string) => fetchJSON<void>(`/api/chapters/${id}/pause`, { method: "POST" }),
    resume: (id: string) => fetchJSON<void>(`/api/chapters/${id}/resume`, { method: "POST" }),
  },
  memory: {
    l4: (bookId: string) => fetchJSON<{ snapshots: L4Snapshot[] }>(`/api/books/${bookId}/memory/l4`),
  },
  audits: {
    list: (bookId: string) => fetchJSON<any[]>(`/api/books/${bookId}/drift-audits`),
  },
  resources: () => fetchJSON<{ available_mb: number; swap_used_pct: number; resource_safe: boolean }>("/api/admin/resources"),
  events: (bookId: string) => fetchJSON<any[]>(`/api/books/${bookId}/events`),
};

export interface Book {
  book_id: string; title: string; status?: string;
  finalized_chapters?: number; finalized_words?: number;
  target_chapters?: number; target_words?: number;
}
export interface OutlineNode {
  node_id: string; chapter_no: number; title: string;
  goal: string; depends_on: any[]; required_beats: string[];
}
export interface Chapter {
  chapter_id: string; chapter_no: number; status: string;
  title: string | null; content: string | null; word_count: number;
  finalized_version: number | null;
}
export interface L4Snapshot {
  id: string; entity_type: string; entity_id: string;
  as_of_chapter: number; state: any; version: number; is_locked: boolean;
}
