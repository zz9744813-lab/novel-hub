const BASE = "";
const TOKEN_KEY = "novelforge_admin_token";
const DRAFT_KEY = "novelforge_admin_token_draft";
const REMEMBER_KEY = "novelforge_admin_token_remember";

// Optional build-time token: set VITE_ADMIN_TOKEN in frontend/.env to have the
// login form pre-filled (and auto-submitted) on fresh browsers.
const EMBEDDED_TOKEN = String(import.meta.env.VITE_ADMIN_TOKEN || "").trim();

export function getEmbeddedToken(): string {
  return EMBEDDED_TOKEN;
}

function readStore(key: string): string | null {
  try {
    return localStorage.getItem(key) ?? sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStore(key: string, value: string, persistent: boolean) {
  try {
    sessionStorage.removeItem(key);
    localStorage.removeItem(key);
    (persistent ? localStorage : sessionStorage).setItem(key, value);
  } catch {
    /* ignore */
  }
}

function removeStore(key: string) {
  try {
    localStorage.removeItem(key);
    sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function isRemembered(): boolean {
  try {
    return localStorage.getItem(REMEMBER_KEY) !== "0";
  } catch {
    return true;
  }
}

export function getAdminToken(): string | null {
  return readStore(TOKEN_KEY);
}

export function setAdminToken(token: string, remember: boolean = isRemembered()) {
  writeStore(TOKEN_KEY, token, remember);
  writeStore(DRAFT_KEY, token, true);
  try {
    localStorage.setItem(REMEMBER_KEY, remember ? "1" : "0");
  } catch {
    /* ignore */
  }
}

/** Last successfully used token (or the embedded one) — used to pre-fill the login input. */
export function getTokenDraft(): string {
  return readStore(DRAFT_KEY) || EMBEDDED_TOKEN || "";
}

export function clearAdminToken() {
  removeStore(TOKEN_KEY);
}

// One-time migration: tokens from the old sessionStorage-only scheme are
// promoted to localStorage so logins survive browser restarts.
try {
  const legacy = sessionStorage.getItem(TOKEN_KEY);
  if (legacy && !localStorage.getItem(TOKEN_KEY)) {
    localStorage.setItem(TOKEN_KEY, legacy);
    if (!localStorage.getItem(DRAFT_KEY)) localStorage.setItem(DRAFT_KEY, legacy);
    sessionStorage.removeItem(TOKEN_KEY);
  }
} catch {
  /* ignore */
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const token = getAdminToken();
  const h: Record<string, string> = {
    ...(extra as Record<string, string>),
  };
  if (token) {
    h["Authorization"] = `Bearer ${token}`;
  }
  return h;
}


async function fetchJSON<T>(url: string, opts?: RequestInit): Promise<T> {
  const headers = authHeaders({
    "Content-Type": "application/json",
    ...(opts?.headers as Record<string, string>),
  });
  const r = await fetch(BASE + url, {
    ...opts,
    headers,
  });
  if (r.status === 401) {
    clearAdminToken();
    window.dispatchEvent(new CustomEvent("novelforge:unauthorized"));
    throw new Error(`401: unauthorized`);
  }
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export type TokenVerifyResult = "ok" | "invalid" | "unreachable";

/** Verify against the backend, distinguishing a wrong token from an unreachable service. */
export async function verifyAdminTokenStatus(token: string): Promise<TokenVerifyResult> {
  try {
    const r = await fetch(BASE + "/api/books", {
      headers: { Authorization: "Bearer " + token },
    });
    if (r.ok) return "ok";
    if (r.status === 401) return "invalid";
    return "unreachable";
  } catch {
    return "unreachable";
  }
}

export async function verifyAdminToken(token: string): Promise<boolean> {
  return (await verifyAdminTokenStatus(token)) === "ok";
}

export async function fetchAuthenticatedAsset(url: string): Promise<string> {
  const r = await fetch(BASE + url, { headers: authHeaders() });
  if (r.status === 401) {
    clearAdminToken();
    window.dispatchEvent(new CustomEvent("novelforge:unauthorized"));
    throw new Error("401: unauthorized");
  }
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return URL.createObjectURL(await r.blob());
}

export interface LibraryBooksResponse { books: any[]; total: number }

export const api = {
  books: {
    list: () => fetchJSON<Book[]>("/api/books"),
    create: (data: { title: string; description?: string; target_chapters?: number }) =>
      fetchJSON<{ book_id: string }>("/api/books", { method: "POST", body: JSON.stringify(data) }),
    get: (id: string) => fetchJSON<Book>(`/api/books/${id}`),
    delete: (id: string) => fetchJSON<{ deleted: boolean; book_id: string }>(`/api/books/${id}`, { method: "DELETE" }),
    generateCover: (id: string) =>
      fetchJSON<{ status: string; width: number; height: number; cover_url: string; thumb_url: string }>(
        `/api/books/${id}/generate-cover`,
        { method: "POST" }
      ),
    exportDownload: async (bookId: string, filenameHint?: string) => {
      const r = await fetch(BASE + `/api/books/${bookId}/export`, {
        headers: authHeaders(),
      });
      if (r.status === 401) {
        clearAdminToken();
        window.dispatchEvent(new CustomEvent("novelforge:unauthorized"));
        throw new Error("401: unauthorized");
      }
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename="?([^";]+)"?/);
      const name = m?.[1] || `${filenameHint || "novel"}.txt`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
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
        headers: authHeaders(),
        body: formData,
      });
      if (r.status === 401) {
        clearAdminToken();
        window.dispatchEvent(new CustomEvent("novelforge:unauthorized"));
        throw new Error(`401: unauthorized`);
      }
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
  planning: {
    get: (bookId: string) =>
      fetchJSON<{ status: string; outline_version_id: string | null; version?: number; draft?: any }>(
        `/api/books/${bookId}/planning`
      ),
    generate: (bookId: string, data: {
      premise: string;
      genre?: string;
      tone?: string;
      themes?: string[];
      target_chapter_count?: number;
    }) =>
      fetchJSON<{ status: string; outline_version_id: string; version: number; draft: any }>(
        `/api/books/${bookId}/planning/generate`,
        { method: "POST", body: JSON.stringify(data) }
      ),
    confirm: (bookId: string, outlineVersionId: string) =>
      fetchJSON<{ status: string; outline_version_id: string; version: number; nodes: number }>(
        `/api/books/${bookId}/planning/confirm`,
        { method: "POST", body: JSON.stringify({ outline_version_id: outlineVersionId }) }
      ),
  },
  chapters: {
    list: (bookId: string) => fetchJSON<ChapterListItem[]>(`/api/books/${bookId}/chapters`),
    run: (bookId: string, chapterNo: number) =>
      fetchJSON<{ chapter_id: string; status: string; chapter_no?: number; run_id?: string }>(
        `/api/books/${bookId}/chapters/${chapterNo}/run`,
        { method: "POST" }
      ),
    runNext: (bookId: string) =>
      fetchJSON<{ chapter_id: string; status: string; chapter_no?: number; run_id?: string }>(
        `/api/books/${bookId}/chapters/next/run`,
        { method: "POST" }
      ),
    get: (id: string) => fetchJSON<Chapter>(`/api/chapters/${id}`),
    pause: (id: string) => fetchJSON<void>(`/api/chapters/${id}/pause`, { method: "POST" }),
    resume: (id: string) => fetchJSON<void>(`/api/chapters/${id}/resume`, { method: "POST" }),
    contextPackages: (chapterId: string) =>
      fetchJSON<ContextPackageSummary[]>(`/api/chapters/${chapterId}/context-packages`),
    runs: (chapterId: string) =>
      fetchJSON<ChapterRunSummary[]>(`/api/chapters/${chapterId}/runs`),
    needsHuman: (chapterId: string) =>
      fetchJSON<NeedsHumanDetail>(`/api/chapters/${chapterId}/needs-human`),
  },
  chapterRuns: {
    get: (runId: string) => fetchJSON<ChapterRunDetail>(`/api/chapter-runs/${runId}`),
  },
  context: {
    get: (id: string) => fetchJSON<ContextPackageDetail>(`/api/context-packages/${id}`),
    promptPreview: (id: string) => fetchJSON<any>(`/api/context-packages/${id}/prompt-preview`),
  },
  models: {
    list: () => fetchJSON<ModelBinding[]>("/api/model-bindings"),
    available: () => fetchJSON<{ models: AvailableModel[]; count: number; source: string }>("/api/models/available"),
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
    get: (id: string) => fetchJSON<any>(`/api/genre-profiles/${id}`),
    listSamples: (bookId: string) => fetchJSON<ReferenceSample[]>(`/api/books/${bookId}/reference-samples`),
    uploadSample: async (bookId: string, file: File, genreHint: string = "") => {
      const formData = new FormData();
      formData.append("file", file);
      if (genreHint) formData.append("genre_hint", genreHint);
      const r = await fetch(BASE + `/api/books/${bookId}/reference-samples`, {
        method: "POST",
        headers: authHeaders(),
        body: formData,
      });
      if (r.status === 401) {
        clearAdminToken();
        window.dispatchEvent(new CustomEvent("novelforge:unauthorized"));
        throw new Error(`401: unauthorized`);
      }
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      return r.json() as Promise<{
        sample_id: string;
        status: string;
        character_count: number;
        filename: string;
      }>;
    },
    analyze: (bookId: string, sampleId: string) =>
      fetchJSON<{
        profile_id: string;
        version: number;
        status: string;
        sanitizer_report: any;
        narrative_person?: string;
        prompt_injection_snippet?: string;
      }>(`/api/books/${bookId}/reference-samples/${sampleId}/analyze`, { method: "POST" }),
    deleteSample: (bookId: string, sampleId: string) =>
      fetchJSON<{ deleted: boolean; sample_id: string; book_id: string }>(
        `/api/books/${bookId}/reference-samples/${sampleId}`,
        { method: "DELETE" }
      ),
    edit: (profileId: string, data: Partial<{ prompt_injection_snippet: string; narrative_person: string }>) =>
      fetchJSON<{ id: string; status: string }>(`/api/genre-profiles/${profileId}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    approve: (profileId: string) =>
      fetchJSON<{ id: string; status: string; version: number }>(
        `/api/genre-profiles/${profileId}/approve`,
        { method: "POST" }
      ),
  },
  research: {
    list: (bookId: string) => fetchJSON<ResearchSessionSummary[]>(`/api/books/${bookId}/research-sessions`),
    get: (sessionId: string) => fetchJSON<ResearchSessionDetail>(`/api/research-sessions/${sessionId}`),
    create: (bookId: string, data: { topic: string; urls?: string[]; search?: boolean; max_results?: number; chapter_id?: string }) =>
      fetchJSON<{
        session_id: string;
        status: string;
        topic: string;
        evidence_count: number;
        evidence_ids: string[];
        plan?: any;
      }>(`/api/books/${bookId}/research-sessions`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    approve: (sessionId: string) =>
      fetchJSON<{ session_id: string; status: string }>(`/api/research-sessions/${sessionId}/approve`, { method: "POST" }),
  },
  researchScrape: {
    sources: () => fetchJSON<ResearchScrapeSource[]>("/api/research/sources"),
    createTask: (data: { source_id: string; target_url: string; book_id?: string }) =>
      fetchJSON<ResearchScrapeTask>("/api/research/tasks", {
        method: "POST",
        body: JSON.stringify(data),
      }),
    tasks: (params?: { book_id?: string; status?: string; limit?: number }) => {
      const q = new URLSearchParams();
      if (params?.book_id) q.set("book_id", params.book_id);
      if (params?.status) q.set("status", params.status);
      if (params?.limit) q.set("limit", String(params.limit));
      const qs = q.toString();
      return fetchJSON<{ tasks: ResearchScrapeTask[]; total: number }>(
        `/api/research/tasks${qs ? `?${qs}` : ""}`
      );
    },
    task: (id: string) => fetchJSON<ResearchScrapeTask>(`/api/research/tasks/${id}`),
    cancelTask: (id: string) =>
      fetchJSON<ResearchScrapeTask>(`/api/research/tasks/${id}/cancel`, { method: "POST" }),
    documents: (taskId: string, limit = 200) =>
      fetchJSON<{ documents: ResearchScrapeDocument[]; total: number }>(
        `/api/research/tasks/${taskId}/documents?limit=${limit}`
      ),
    exportTask: (taskId: string) =>
      fetchJSON<ResearchScrapeExport>(`/api/research/tasks/${taskId}/exports`, {
        method: "POST",
        body: JSON.stringify({ format: "txt" }),
      }),
    exportDownload: async (exportId: string, filenameHint?: string) => {
      const r = await fetch(BASE + `/api/research/exports/${exportId}/download`, {
        headers: authHeaders(),
      });
      if (r.status === 401) {
        clearAdminToken();
        window.dispatchEvent(new CustomEvent("novelforge:unauthorized"));
        throw new Error("401: unauthorized");
      }
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename="?([^";]+)"?/);
      const name = m?.[1] || `${filenameHint || "research-export"}.txt`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    },
    importReference: (
      taskId: string,
      data: { book_id: string; mode: "all" | "selected"; document_ids?: string[] }
    ) =>
      fetchJSON<{ sample_ids: string[]; created: number; deduped: number }>(
        `/api/research/tasks/${taskId}/import-reference`,
        { method: "POST", body: JSON.stringify(data) }
      ),
  },
  memory: {
    l4: (bookId: string) => fetchJSON<{ snapshots: L4Snapshot[] }>(`/api/books/${bookId}/memory/l4`),
  },
  characters: {
    list: (bookId: string) =>
      fetchJSON<{ characters: CharacterSummary[] }>(`/api/books/${bookId}/characters`),
  },
  coreAnchors: {
    list: (bookId: string, characterId: string) =>
      fetchJSON<{ anchors: CoreAnchor[] }>(
        `/api/books/${bookId}/characters/${characterId}/core-anchors`
      ),
    create: (
      bookId: string,
      characterId: string,
      data: {
        anchor_code: string;
        anchor_type?: string;
        statement: string;
        priority?: number;
        rigidity?: number;
      }
    ) =>
      fetchJSON<{ anchor_id: string; anchor_code: string; status: string }>(
        `/api/books/${bookId}/characters/${characterId}/core-anchors`,
        { method: "POST", body: JSON.stringify(data) }
      ),
    update: (
      anchorId: string,
      data: Partial<{
        statement: string;
        anchor_type: string;
        priority: number;
        rigidity: number;
        status: string;
        is_locked: boolean;
      }>
    ) =>
      fetchJSON<{ anchor_id: string; status: string }>(`/api/core-anchors/${anchorId}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    remove: (anchorId: string) =>
      fetchJSON<{ anchor_id: string; status: string }>(`/api/core-anchors/${anchorId}`, {
        method: "DELETE",
      }),
  },
  sceneContracts: {
    list: (chapterId: string) =>
      fetchJSON<{ contracts: SceneContractItem[] }>(`/api/chapters/${chapterId}/scene-contracts`),
    get: (contractId: string) =>
      fetchJSON<SceneContractItem>(`/api/scene-contracts/${contractId}`),
    simulate: (contractId: string) =>
      fetchJSON<SceneSimulationResult>(`/api/scene-contracts/${contractId}/simulate`, {
        method: "POST",
      }),
  },
  causalGraph: {
    get: (chapterId: string) => fetchJSON<CausalGraph>(`/api/chapters/${chapterId}/causal-graph`),
    audit: (chapterId: string) =>
      fetchJSON<{ chapter_id: string; chapter_no: number; report: CounterfactualReport }>(
        `/api/chapters/${chapterId}/counterfactual-audit`,
        { method: "POST" }
      ),
  },
  audits: {
    list: (bookId: string) => fetchJSON<any[]>(`/api/books/${bookId}/drift-audits`),
  },
  resources: () => fetchJSON<{ available_mb: number; swap_used_pct: number; resource_safe: boolean }>("/api/admin/resources"),
  events: (bookId: string) => fetchJSON<any[]>(`/api/books/${bookId}/events`),
  tasks: {
    list: (params?: { task_type?: string; status?: string; book_id?: string; page?: number; page_size?: number }) => {
      const q = new URLSearchParams();
      for (const [key, value] of Object.entries(params || {})) {
        if (value !== undefined && value !== "") q.set(key, String(value));
      }
      return fetchJSON<TaskListResponse>(`/api/tasks${q.toString() ? `?${q}` : ""}`);
    },
    get: (taskId: string) => fetchJSON<TaskItem>(`/api/tasks/${encodeURIComponent(taskId)}`),
    operate: (taskId: string, action: string) =>
      fetchJSON<{ task_id: string; status: string }>(
        `/api/tasks/${encodeURIComponent(taskId)}/${encodeURIComponent(action)}`,
        { method: "POST" }
      ),
  },
  library: {
    books: () => fetchJSON<LibraryBooksResponse>("/api/library/books"),
    bookHome: (bookId: string) => fetchJSON<any>(`/api/library/books/${bookId}/home`),
    contextPreview: (bookId: string, chapterNo = 1, agentRole = "draft_writer") =>
      fetchJSON<any>(
        `/api/library/books/${bookId}/context-preview?chapter_no=${chapterNo}&agent_role=${encodeURIComponent(agentRole)}`
      ),
  },
  imports: {
    create: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      const r = await fetch(BASE + "/api/import-sessions", {
        method: "POST",
        headers: authHeaders(),
        body: formData,
      });
      if (r.status === 401) {
        clearAdminToken();
        window.dispatchEvent(new CustomEvent("novelforge:unauthorized"));
        throw new Error(`401: unauthorized`);
      }
      if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
      return r.json() as Promise<{
        import_session_id: string;
        status: string;
        progress: number;
        preview_hash: string;
        enqueue_error?: string | null;
        message?: string;
      }>;
    },
    list: (params?: { status?: string; limit?: number }) => {
      const q = new URLSearchParams((params as any) || {}).toString();
      return fetchJSON<{ sessions: any[] }>(`/api/import-sessions?${q}`);
    },
    get: (id: string) => fetchJSON<any>(`/api/import-sessions/${id}`),
    preview: (id: string) => fetchJSON<any>(`/api/import-sessions/${id}/preview`),
    analyze: (id: string) =>
      fetchJSON<{ status: string; import_session_id: string }>(`/api/import-sessions/${id}/analyze`, {
        method: "POST",
      }),
    resolveConflict: (sessionId: string, conflictId: string, optionId: string) =>
      fetchJSON<any>(`/api/import-sessions/${sessionId}/conflicts/${conflictId}/resolve`, {
        method: "POST",
        body: JSON.stringify({ option_id: optionId }),
      }),
    resolveBatch: (sessionId: string, mode: "warnings" | "all_open" = "warnings") =>
      fetchJSON<{ status: string; resolved_count: number; skipped_count: number; session_status?: string }>(
        `/api/import-sessions/${sessionId}/conflicts/resolve-batch`,
        {
          method: "POST",
          body: JSON.stringify({ mode }),
        }
      ),
    commit: (id: string, body: { expected_preview_hash: string; book_overrides?: any }) =>
      fetchJSON<{ book_id: string; status: string; counts?: any }>(`/api/import-sessions/${id}/commit`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    cancel: (sessionId: string) =>
      fetchJSON<{ status: string }>(`/api/import-sessions/${sessionId}/cancel`, { method: "POST" }),
  },
  promptStudio: {
    agents: () => fetchJSON<{ agents: any[]; context_kinds?: string[] }>("/api/prompt-studio/agents"),
    contracts: () => fetchJSON<{ contracts: any[] }>("/api/prompt-studio/contracts"),
    list: () => fetchJSON<{ templates: any[] }>("/api/prompt-studio/templates"),
    templates: (agentRole?: string) =>
      fetchJSON<{ templates: any[] }>(
        `/api/prompt-studio/templates${agentRole ? `?agent_role=${encodeURIComponent(agentRole)}` : ""}`
      ),
    get: (id: string) => fetchJSON<any>(`/api/prompt-studio/templates/${id}`),
    getTemplate: (id: string) => fetchJSON<any>(`/api/prompt-studio/templates/${id}`),
    create: (body: any) =>
      fetchJSON<any>("/api/prompt-studio/templates", { method: "POST", body: JSON.stringify(body) }),
    createTemplate: (body: any) =>
      fetchJSON<any>("/api/prompt-studio/templates", { method: "POST", body: JSON.stringify(body) }),
    seedDefaults: () =>
      fetchJSON<{ created: any[]; skipped: string[]; created_count: number }>(
        "/api/prompt-studio/templates/seed-defaults",
        { method: "POST" }
      ),
    activate: (id: string) =>
      fetchJSON<any>(`/api/prompt-studio/templates/${id}/activate`, { method: "POST" }),
    update: (id: string, body: any) =>
      fetchJSON<any>(`/api/prompt-studio/templates/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    clone: (id: string) =>
      fetchJSON<any>(`/api/prompt-studio/templates/${id}/clone`, { method: "POST" }),
    archive: (id: string) =>
      fetchJSON<any>(`/api/prompt-studio/templates/${id}/archive`, { method: "POST" }),
    test: (id: string) =>
      fetchJSON<any>(`/api/prompt-studio/templates/${id}/test`, { method: "POST" }),
    testStructure: (id: string) =>
      fetchJSON<any>(`/api/prompt-studio/templates/${id}/test-structure`, { method: "POST" }),
    compatibility: (id: string) => fetchJSON<any>(`/api/prompt-studio/templates/${id}/compatibility`),
    compiledPreview: (id: string) =>
      fetchJSON<any>(`/api/prompt-studio/templates/${id}/compiled-preview`),
  },

};

export interface TaskError {
  code?: string | null;
  detail?: unknown;
}
export interface TaskItem {
  task_id: string;
  task_type: "chapter" | "import" | "research";
  entity_id: string;
  book_id?: string | null;
  book_title?: string | null;
  chapter_id?: string | null;
  chapter_no?: number | null;
  status: string;
  progress?: number | null;
  current_step?: string | null;
  control_requested?: string | null;
  error?: TaskError | null;
  actions: string[];
  created_at?: string | null;
  updated_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  topic?: string | null;
}
export interface TaskListResponse {
  items: TaskItem[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
  task_types: string[];
}

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
export interface ChapterListItem {
  chapter_id: string;
  chapter_no: number;
  status: string;
  title: string | null;
  word_count: number;
}
export interface ChapterRunSummary { run_id: string; status: string; current_step?: string | null; chapter_no?: number; }
export interface NeedsHumanDetail { chapter_id: string; status: string; issues?: any[]; detail?: any; run?: any; active_run_id?: string | null; }
export interface ChapterRunDetail extends ChapterRunSummary { book_id?: string; error_code?: string | null; error_detail?: any; }
export interface L4Snapshot {
  id: string; entity_type: string; entity_id: string;
  as_of_chapter: number; state: any; version: number; is_locked: boolean;
}

// ---- v9.0 CCNE types ----
export interface CharacterSummary {
  id: string;
  name: string;
  role: string | null;
  description: string | null;
  anchor_count: number;
}

export interface CoreAnchor {
  id: string;
  anchor_code: string;
  anchor_type: string;
  statement: string;
  priority: number;
  rigidity: number;
  source_kind: string;
  status: string;
  is_locked: boolean;
}

export interface SceneContractItem {
  id: string;
  scene_no: number;
  contract_hash: string;
  status: string;
  validation?: any;
  summary?: {
    dramatic_goal?: string | null;
    pov_character_id?: string | null;
    event_count?: number;
    edge_count?: number;
    belief_count?: number;
    appraisal_count?: number;
    hard_effect_count?: number;
  };
  contract?: any;
}

export interface SceneSimulationResult {
  contract_id: string;
  scene_no: number;
  ok: boolean;
  findings: Array<{ code: string; severity: string; message: string; [k: string]: any }>;
  next_state: any;
}

export interface CausalGraphNode {
  id: string;
  scene_id: string;
  event_type: string;
  excerpt: string;
  subjects: string[];
}

export interface CausalGraphLink {
  source: string;
  target: string;
  relation: string;
  mode: string;
  mechanism?: string | null;
}

export interface CausalGraph {
  chapter_id: string;
  chapter_no: number;
  nodes: CausalGraphNode[];
  links: CausalGraphLink[];
  stats: { event_count: number; edge_count: number; hard_edge_count: number };
}

export interface CounterfactualFinding {
  removed_event_key: string;
  checked_target_key: string;
  support_after_removal: string;
  classification: string;
  remaining_support_keys: string[];
  detail: string;
}

export interface CounterfactualReport {
  ok: boolean;
  audited_events: string[];
  findings: CounterfactualFinding[];
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

export interface AvailableModel {
  id: string;
  owned_by?: string | null;
  object?: string;
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
  created_at: string | null;
  narrative_person?: string | null;
  technique_tags?: string[];
  prompt_injection_snippet?: string;
  sanitizer_report?: any;
  approved_by?: string | null;
  approved_at?: string | null;
}

export interface ReferenceSample {
  id: string;
  filename: string;
  status: string;
  character_count: number;
  genre_hint?: string | null;
  uploaded_at?: string | null;
}

export interface ResearchSessionSummary {
  id: string;
  status: string;
  requested_topic: string;
  trigger_type: string;
  approved_by?: string | null;
  approved_at?: string | null;
  completed_at?: string | null;
}

export interface ResearchSessionDetail {
  id: string;
  book_id: string;
  status: string;
  requested_topic: string;
  evidence: Array<{
    id: string;
    source_url: string;
    source_title?: string | null;
    summary: string;
    status: string;
    confidence: number;
    trust_tier: string;
  }>;
}

export interface ResearchScrapeSource {
  id: string;
  code: string;
  name: string;
  base_url: string;
  chapter_list_selector: string | null;
  title_selector: string | null;
  content_selector: string;
  pagination_selector: string | null;
  encoding: string;
  rate_limit: number;
  enabled: boolean;
  verification_status: string;
  last_verified_at: string | null;
  config: Record<string, unknown>;
}

export interface ResearchScrapeTask {
  id: string;
  book_id: string | null;
  source_id: string;
  source_code: string | null;
  source_name: string | null;
  target_url: string;
  status: string;
  progress: number;
  discovered_count: number;
  completed_count: number;
  current_url: string | null;
  error_code: string | null;
  error_detail: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ResearchScrapeDocument {
  id: string;
  task_id: string;
  ordinal: number;
  title: string;
  source_url: string;
  char_count: number;
  content_hash: string;
  metadata: Record<string, unknown>;
}

export interface ResearchScrapeExport {
  id: string;
  task_id: string;
  format: string;
  file_path: string;
  content_hash: string;
  byte_size: number;
  document_count: number;
  download_url: string;
}
