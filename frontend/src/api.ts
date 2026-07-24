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
      fetchJSON<{ outline_version_id: string; status: string; errors?: string[]; version?: number }>(
        `/api/books/${bookId}/outlines/parse`, { method: "POST", body: JSON.stringify(data) }
      ),
    upload: async (bookId: string, file: File, targetChapters: number = 500) => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("target_chapter_count", String(targetChapters));
      const r = await fetch(BASE + `/api/books/${bookId}/outlines/upload`, {
        method: "POST",
        body: formData,
      });
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      return r.json() as Promise<{
        outline_version_id: string;
        status: string;
        errors?: string[];
        version?: number;
        filename?: string;
        chars?: number;
      }>;
    },
    graph: (bookId: string) =>
      fetchJSON<{
        nodes: OutlineNode[];
        outline_version_id: string | null;
        version: number | null;
        status: string | null;
      }>(`/api/books/${bookId}/outline-graph`),
    approve: (bookId: string, version: number) =>
      fetchJSON<{ status: string }>(`/api/books/${bookId}/outlines/${version}/approve`, { method: "POST" }),
  },
  chapters: {
    run: (bookId: string, chapterNo: number) =>
      fetchJSON<{ chapter_id: string; status: string }>(`/api/books/${bookId}/chapters/${chapterNo}/run`, { method: "POST" }),
    get: (id: string) => fetchJSON<Chapter>(`/api/chapters/${id}`),
    pause: (id: string) => fetchJSON<void>(`/api/chapters/${id}/pause`, { method: "POST" }),
    resume: (id: string) => fetchJSON<void>(`/api/chapters/${id}/resume`, { method: "POST" }),
    contextPackages: (chapterId: string) =>
      fetchJSON<ContextPackageSummary[]>(`/api/chapters/${chapterId}/context-packages`),
  },
  context: {
    get: (id: string) => fetchJSON<ContextPackageDetail>(`/api/context-packages/${id}`),
  },
  models: {
    list: () => fetchJSON<ModelBinding[]>("/api/model-bindings"),
    update: (id: string, data: Partial<ModelBinding> & { reason?: string }) =>
      fetchJSON<{ id: string; status: string }>(`/api/model-bindings/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    changeLog: () => fetchJSON<ModelChangeLogEntry[]>("/api/model-change-log"),
    routeEvents: (runId: string) =>
      fetchJSON<ModelRouteEvent[]>(`/api/runs/${runId}/model-route-events`),
  },
  genre: {
    list: (bookId: string) => fetchJSON<GenreProfileSummary[]>(`/api/books/${bookId}/genre-profiles`),
  },
  research: {
    approve: (sessionId: string) =>
      fetchJSON<{ session_id: string; status: string }>(`/api/research-sessions/${sessionId}/approve`, { method: "POST" }),
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

export interface ModelBinding {
  id: string;
  scope_type: string;
  scope_id: string | null;
  agent_role: string;
  provider: string;
  primary_model: string;
  fallback_model: string | null;
  reasoning_mode: string;
  version: number;
  updated_by: string;
  updated_at: string;
}

export interface ModelChangeLogEntry {
  id: string;
  agent_role: string;
  old_model: string | null;
  new_model: string;
  reason: string;
  changed_by: string;
  changed_at: string;
}

export interface ModelRouteEvent {
  attempt_no: number;
  configured_model: string;
  actual_model: string;
  route_type: string;
  reason: string | null;
}

export interface ContextPackageSummary {
  id: string;
  run_id: string;
  attempt_no: number;
  agent_role: string;
  provider: string;
  model: string;
  publish_state: string;
  block_reason: string | null;
  assembled_at: string;
}

export interface ContextPackageDetail extends ContextPackageSummary {
  prompt_version: string;
  prompt_template_hash: string;
  rendered_prompt_hash: string;
  assembly_manifest: any;
  l4_entity_refs: any[];
  assembled_token_estimate: number;
}

export interface GenreProfileSummary {
  id: string;
  version: number;
  status: string;
  created_at: string;
}
