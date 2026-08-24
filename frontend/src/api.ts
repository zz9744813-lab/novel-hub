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

// ── v9.3 Editorial Learning Loop (ELL) types ──────────────────────────

export interface EditorialPolicy {
  book_id: string;
  mode: "blocking" | "windowed" | "learning_only" | string;
  max_unreviewed_ahead: number;
  review_sampling_mode: string;
  require_review: boolean;
  good_score_threshold: number;
  auto_pause_good_rate_threshold: number;
  auto_pause_consecutive_bad: number;
  rubric_template_id: string | null;
  experience_auto_activation: boolean;
  low_risk_auto_promote: boolean;
}

export interface EditorialQueueCard {
  chapter_id: string;
  book_id: string;
  book_title: string | null;
  chapter_no: number;
  title: string | null;
  editorial_status: string;
  ai_status: string;
  latest_version_id: string | null;
  ai_issue_count: number;
  waiting_hours: number;
  rounds: number;
}

export interface EditorialReviewRound {
  id: string;
  book_id: string;
  chapter_id: string;
  chapter_version_id: string;
  round_no: number;
  status: string;
  verdict: string | null;
  score_total: number | null;
  grade: string | null;
  rubric_scores: Record<string, number> | null;
  overall_comment: string | null;
  reviewer_kind: string;
  reviewer_id: string | null;
  ai_issue_dispositions: Record<string, string>;
  submitted_at: string | null;
  completed_at: string | null;
}

export interface EditorialAnnotationInput {
  annotation_type: string;
  category?: string | null;
  severity?: string | null;
  scope?: string;
  scene_no?: number | null;
  paragraph_key?: number | null;
  start_offset?: number | null;
  end_offset?: number | null;
  quoted_text?: string | null;
  comment?: string | null;
  suggested_text?: string | null;
  is_blocking?: boolean;
  tags?: string[];
}

export interface EditorialAnnotation {
  id: string;
  review_round_id: string;
  annotation_type: string;
  category: string | null;
  severity: string;
  scope: string;
  scene_no: number | null;
  paragraph_key: number | null;
  start_offset: number | null;
  end_offset: number | null;
  quoted_text: string | null;
  comment: string | null;
  suggested_text: string | null;
  is_blocking: boolean;
  ai_issue_match_ids: unknown[];
  tags: string[];
  resolution_status: string;
  resolved_by_version_id: string | null;
}

export interface EditorialAiIssue {
  id: string;
  issue_type: string;
  severity: string;
  evidence: string;
  paragraph_id: string;
  repair_instruction: string | null;
  disposition: string | null;
}

export interface EditorialRubricDimension {
  key: string;
  name: string;
  weight: number;
  anchors: Record<string, string>;
}

export interface EditorialReviewDetail {
  round: EditorialReviewRound;
  chapter: { id?: string; chapter_no?: number; title?: string | null; [k: string]: unknown };
  version_content: string;
  paragraphs: string[];
  rubric: EditorialRubricDimension[];
  annotations: EditorialAnnotation[];
  ai_issues: EditorialAiIssue[];
  version_lineage: Array<Record<string, unknown>>;
}

export interface EditorialRevisionStatus {
  editorial_status: string;
  latest_version: { id: string; version: number; revision_origin: string | null; parent_version_id: string | null } | null;
  revised_from_round: string;
}

export interface EditorialMetrics {
  total_reviewed: number;
  first_pass_accepted: number;
  first_pass_yield: number | null;
  score_trend: Array<{ chapter_no: number | null; round_no: number; score: number | null; grade: string | null; verdict: string | null }>;
  revision_depth: Record<string, number>;
  category_pareto: Record<string, number>;
  root_causes: Record<string, number>;
  ai_calibration: {
    confirmed: number;
    dismissed: number;
    corrected: number;
    agreement: number | null;
    severe_human_issues: number;
    escaped: number;
    escape_rate: number | null;
  };
  status_distribution: Record<string, number>;
  consecutive_bad: number;
  window_good_rate: number | null;
  experience_cards: { active: number; candidate: number; locked: number; rejected: number };
  annotation_total: number;
}

export interface EditorialExperienceCardItem {
  id: string;
  book_id: string | null;
  rule_type: string;
  scope_type: string;
  category: string;
  trigger_conditions: Record<string, unknown>;
  instruction: string;
  rationale: string | null;
  target_components: string[];
  support_count: number;
  contradiction_count: number;
  confidence: number;
  status: string;
  is_locked: boolean;
  effective_from_chapter: number | null;
  last_confirmed_at: string | null;
  source_annotation_ids: string[];
}

export interface EditorialInsightItem {
  id: string;
  annotation_id: string;
  normalized_category: string;
  human_intent: string | null;
  symptom: string | null;
  root_cause_component: string;
  remediation_level: string;
  confidence: number;
}

export interface EditorialProposalItem {
  id: string;
  proposal_type: string;
  target_component: string;
  risk_level: string;
  reason: string | null;
  candidate_patch: Record<string, unknown>;
  status: string;
  approved_by: string | null;
  approved_at: string | null;
  experiment_id: string | null;
  promoted_at: string | null;
  effective_from_chapter: number | null;
}

export interface EditorialParetoCandidate {
  name: string;
  source: string;
  pass_rate: number | null;
  retention: number;
  changed: number;
  pareto_rank: number;
}

export interface EditorialExperimentItem {
  id: string;
  proposal_id: string | null;
  status: string;
  recommendation: string | null;
  metrics_baseline: Record<string, unknown>;
  metrics_candidate: Record<string, unknown>;
  case_count: number;
  hard_pass: boolean | null;
  started_at: string | null;
}

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
  writingSessions: {
    current: (bookId: string) =>
      fetchJSON<{ session: WritingSessionView | null }>(`/api/books/${bookId}/writing-sessions/current`),
    start: (bookId: string, data: any, idempotencyKey?: string) =>
      fetchJSON<WritingSessionView>(
        `/api/books/${bookId}/writing-sessions`,
        {
          method: "POST",
          body: JSON.stringify(data),
          headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
        }
      ),
    get: (sessionId: string) => fetchJSON<WritingSessionView>(`/api/writing-sessions/${sessionId}`),
    pause: (sessionId: string) =>
      fetchJSON<WritingSessionView>(`/api/writing-sessions/${sessionId}/pause`, { method: "POST" }),
    resume: (sessionId: string) =>
      fetchJSON<WritingSessionView>(`/api/writing-sessions/${sessionId}/resume`, { method: "POST" }),
    cancel: (sessionId: string) =>
      fetchJSON<WritingSessionView>(`/api/writing-sessions/${sessionId}/cancel`, { method: "POST" }),
    extend: (sessionId: string, extendMinutes: number) =>
      fetchJSON<WritingSessionView>(`/api/writing-sessions/${sessionId}/extend`, {
        method: "POST",
        body: JSON.stringify({ extend_minutes: extendMinutes }),
      }),
    history: (bookId: string) =>
      fetchJSON<{ items: WritingSessionView[] }>(`/api/books/${bookId}/writing-sessions`),
    chapters: (sessionId: string) =>
      fetchJSON<{ items: SessionChapterRun[] }>(`/api/writing-sessions/${sessionId}/chapters`),
  },
  modelCenter: {
    overview: () => fetchJSON<any>("/api/model-center/health"),
    models: () => fetchJSON<{ items: any[] }>("/api/model-center/models"),
    modelDetail: (catalogId: string) => fetchJSON<any>(`/api/model-center/models/${catalogId}`),
    probes: (catalogId: string) => fetchJSON<any>(`/api/model-center/models/${catalogId}/probes`),
    probeNow: (catalogId: string) => fetchJSON<any>(`/api/model-center/models/${catalogId}/probe`, { method: "POST" }),
    probeAll: () => fetchJSON<any>("/api/model-center/probe-all", { method: "POST" }),
    sync: () => fetchJSON<any>("/api/model-center/sync", { method: "POST" }),
    roleRanking: (role: string) => fetchJSON<any>(`/api/model-center/role-ranking/${role}`),
    routing: () => fetchJSON<{ items: any[] }>("/api/model-center/routing"),
    routesCurrent: () => fetchJSON<{ items: any[] }>("/api/model-center/routes/current"),
    timeline: () => fetchJSON<any>("/api/model-center/routes/timeline"),
    recalculate: () => fetchJSON<any>("/api/model-center/routes/recalculate", { method: "POST" }),
    enableAutoRoute: (catalogId: string) =>
      fetchJSON<any>(`/api/model-center/models/${catalogId}/enable`, { method: "POST" }),
    patchCapabilities: (catalogId: string, data: any) =>
      fetchJSON<any>(`/api/model-center/models/${catalogId}/capabilities`, { method: "PATCH", body: JSON.stringify(data) }),
    policies: () => fetchJSON<any>("/api/model-center/policies"),
    createPolicy: (data: any) =>
      fetchJSON<any>("/api/model-center/policies", { method: "POST", body: JSON.stringify(data) }),
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
  styleProfile: {
    analyze: (bookId: string, data?: { genre_hint?: string; text?: string }) =>
      fetchJSON<StyleProfileOut>(`/api/books/${bookId}/style-profile/analyze`, {
        method: "POST",
        body: JSON.stringify(data || {}),
      }),
    get: (bookId: string) =>
      fetchJSON<{ status: string; profile: StyleProfileOut | null }>(
        `/api/books/${bookId}/style-profile`
      ),
    scoreChapter: (bookId: string, chapterNo: number, data?: { content?: string }) =>
      fetchJSON<ChapterStyleScoreOut>(
        `/api/books/${bookId}/chapters/${chapterNo}/style-score`,
        { method: "POST", body: JSON.stringify(data || {}) }
      ),
    verifyChapter: (bookId: string, chapterNo: number, data?: { content?: string }) =>
      fetchJSON<StyleVerifyOut>(
        `/api/books/${bookId}/chapters/${chapterNo}/style-verify`,
        { method: "POST", body: JSON.stringify(data || {}) }
      ),
    drift: (bookId: string) =>
      fetchJSON<{ status: string; drift: StyleDriftOut | null; chapter_count: number }>(
        `/api/books/${bookId}/style-drift`
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
    health: () => fetchJSON<ResearchHealth>("/api/research/health"),
    probe: (sourceId: string, testUrl: string) =>
      fetchJSON<ResearchProbeResult>(`/api/research/sources/${sourceId}/probe`, {
        method: "POST",
        body: JSON.stringify({ test_url: testUrl }),
      }),
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
  system: {
    version: () =>
      fetchJSON<{
        app_version: string;
        git_sha: string;
        pipeline_version: string;
        alembic_revision: string;
      }>("/api/system/version"),
  },
  editorial: {
    policy: (bookId: string) => fetchJSON<EditorialPolicy>(`/api/books/${bookId}/editorial/policy`),
    updatePolicy: (bookId: string, data: Partial<EditorialPolicy>) =>
      fetchJSON<EditorialPolicy>(`/api/books/${bookId}/editorial/policy`, {
        method: "PUT",
        body: JSON.stringify(data),
      }),
    queue: (filter: string, bookId?: string) => {
      const q = new URLSearchParams({ filter });
      const base = bookId ? `/api/books/${bookId}/editorial/review-queue` : "/api/editorial/review-queue";
      return fetchJSON<EditorialQueueCard[]>(`${base}?${q.toString()}`);
    },
    createRound: (chapterId: string) =>
      fetchJSON<EditorialReviewRound>(`/api/chapters/${chapterId}/editorial/reviews`, { method: "POST" }),
    roundDetail: (reviewId: string) => fetchJSON<EditorialReviewDetail>(`/api/editorial/reviews/${reviewId}`),
    listRounds: (chapterId: string) =>
      fetchJSON<EditorialReviewRound[]>(`/api/chapters/${chapterId}/editorial/reviews`),
    submitRound: (
      reviewId: string,
      data: { verdict: string; score_total?: number; rubric_scores?: Record<string, number>; quick_grade?: string; overall_comment?: string }
    ) =>
      fetchJSON<EditorialReviewRound>(`/api/editorial/reviews/${reviewId}/submit`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    addAnnotation: (reviewId: string, data: EditorialAnnotationInput) =>
      fetchJSON<EditorialAnnotation>(`/api/editorial/reviews/${reviewId}/annotations`, {
        method: "POST",
        body: JSON.stringify(data),
      }),
    patchAnnotation: (annotationId: string, data: Partial<EditorialAnnotationInput> & { resolution_status?: string }) =>
      fetchJSON<EditorialAnnotation>(`/api/editorial/annotations/${annotationId}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    deleteAnnotation: (annotationId: string) =>
      fetchJSON<void>(`/api/editorial/annotations/${annotationId}`, { method: "DELETE" }),
    dispositionAiIssue: (reviewId: string, issueId: string, action: "confirm" | "dismiss" | "correct") =>
      fetchJSON<EditorialReviewRound>(`/api/editorial/reviews/${reviewId}/ai-issues/${issueId}/${action}`, {
        method: "POST",
      }),
    requestRevision: (reviewId: string, remediationLevel?: string) =>
      fetchJSON<{ status: string }>(`/api/editorial/reviews/${reviewId}/revision`, {
        method: "POST",
        body: JSON.stringify(remediationLevel ? { remediation_level: remediationLevel } : {}),
      }),
    revisionStatus: (reviewId: string) =>
      fetchJSON<EditorialRevisionStatus>(`/api/editorial/reviews/${reviewId}/revision-status`),
    metrics: (bookId: string) =>
      fetchJSON<EditorialMetrics>(`/api/books/${bookId}/editorial/metrics`),
    experienceCards: (bookId: string, status?: string) => {
      const q = status ? `?status=${encodeURIComponent(status)}` : "";
      return fetchJSON<EditorialExperienceCardItem[]>(
        `/api/books/${bookId}/editorial/experience-cards${q}`
      );
    },
    updateExperienceCard: (cardId: string, data: { status: string; is_locked?: boolean | null }) =>
      fetchJSON<EditorialExperienceCardItem>(`/api/editorial/experience-cards/${cardId}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      }),
    previewExperience: (
      bookId: string,
      data: { chapter_no?: number | null; scene_type?: string | null; character_ids?: string[]; include_candidates?: boolean }
    ) =>
      fetchJSON<{ cards: EditorialExperienceCardItem[]; prompt_block: string }>(
        `/api/books/${bookId}/editorial/experience-cards/preview`,
        { method: "POST", body: JSON.stringify(data) }
      ),
    insights: (bookId: string) =>
      fetchJSON<EditorialInsightItem[]>(`/api/books/${bookId}/editorial/insights`),
    proposals: (bookId: string) =>
      fetchJSON<EditorialProposalItem[]>(`/api/books/${bookId}/editorial/proposals`),
    reviewProposal: (proposalId: string, approve: boolean, reviewer?: string) =>
      fetchJSON<{ status: string }>(`/api/editorial/proposals/${proposalId}/review`, {
        method: "POST",
        body: JSON.stringify({ approve, reviewer }),
      }),
    promoteProposal: (proposalId: string, effectiveFromChapter?: number) =>
      fetchJSON<{ status: string; effective_from_chapter: number | null }>(
        `/api/editorial/proposals/${proposalId}/promote`,
        { method: "POST", body: JSON.stringify({ effective_from_chapter: effectiveFromChapter ?? null }) }
      ),
    rollbackProposal: (proposalId: string) =>
      fetchJSON<{ status: string }>(`/api/editorial/proposals/${proposalId}/rollback`, {
        method: "POST",
        body: JSON.stringify({}),
      }),
    experiments: (bookId: string) =>
      fetchJSON<EditorialExperimentItem[]>(`/api/books/${bookId}/editorial/experiments`),
    createExperiment: (
      bookId: string,
      proposalId?: string | null,
      useGepa?: boolean
    ) =>
      fetchJSON<{
        id: string;
        status: string;
        recommendation: string | null;
        pareto_candidates: EditorialParetoCandidate[];
      }>(`/api/books/${bookId}/editorial/experiments`, {
        method: "POST",
        body: JSON.stringify({ proposal_id: proposalId ?? null, use_gepa: useGepa ?? false }),
      }),
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
export interface WritingSessionView {
  id: string;
  book_id: string;
  status: string; // created|running|pausing|paused|waiting_editorial|blocked|completed|cancelled|failed
  control_requested: string;
  mode: string;
  requested_duration_minutes?: number | null;
  started_at?: string | null;
  deadline_at?: string | null;
  current_chapter_id?: string | null;
  current_chapter_no?: number | null;
  current_chapter_run_id?: string | null;
  chapters_started: number;
  chapters_completed: number;
  words_generated: number;
  stop_reason?: string | null;
  stop_detail?: any;
  policy_snapshot?: any;
  model_route_plan_id?: string | null;
  model_routing_policy_version?: number | null;
  model_preflight_status?: string | null;
  model_preflight_detail?: any;
  editorial_backlog?: number | null;
  editorial_backlog_limit?: number | null;
  recent_first_pass?: { reviewed: number; good: number; rate: number } | null;
  paused_at?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}
export interface SessionChapterRun {
  run_id: string;
  chapter_id: string;
  chapter_no: number;
  status: string;
  current_step?: string | null;
  error_code?: string | null;
  words?: number | null;
  editorial_status?: string | null;
  created_at?: string | null;
  finished_at?: string | null;
}
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

export interface StyleMetricVector {
  surface: Record<string, number>;
  rhythm: Record<string, number>;
  dialogue: Record<string, number>;
  emotion: Record<string, number>;
  meta?: Record<string, number>;
}

export interface StyleProfileOut {
  id: string;
  book_id: string;
  version: number;
  status: string;
  metric_vector: StyleMetricVector;
  metric_ranges: Record<string, { target: number; preferred_min: number; preferred_max: number; hard_min: number; hard_max: number }>;
  fingerprint: number[];
  narrative_profile: Record<string, unknown>;
  dialogue_profile: Record<string, unknown>;
  rhythm_profile: Record<string, unknown>;
  emotion_expression_profile: Record<string, unknown>;
  technique_profile: Record<string, unknown>;
  scene_mode_profiles: Record<string, unknown>;
  confidence_by_dimension: Record<string, unknown>;
  analyzer_version: string | null;
  metric_engine_version: string | null;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string | null;
}

export interface ChapterStyleScoreOut {
  chapter_no: number;
  surface_score: number;
  rhythm_score: number;
  dialogue_score: number;
  narrative_score: number;
  emotion_score: number;
  voice_score: number;
  overall_score: number;
  distance_to_profile: number;
}

export interface StyleFinding {
  code: string;
  target: number[];
  actual: number;
  severity: string;
}

export interface StyleVerifyOut {
  passed: boolean;
  findings: StyleFinding[];
  metrics: StyleMetricVector;
}

export interface StyleDriftOut {
  style_distance_mean: number;
  style_distance_max: number;
  latest_distance: number;
  drift_triggered: boolean;
  per_chapter: number[];
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

export interface ResearchProbeResult {
  status: string; // passed / failed / blocked
  http_status: number | null;
  final_url: string | null;
  latency_ms: number | null;
  response_bytes: number | null;
  page_type: string; // book / chapter / generic
  title_hit_count: number;
  list_link_count: number;
  content_hit_count: number;
  extracted_chars: number;
  anti_bot_type: string | null;
  encoding_detected: string | null;
  diagnostics: string[];
  candidate_selectors: { selector: string; chars: number }[];
}

export interface ResearchHealth {
  api: string;
  enabled_sources: number;
  verified_sources: number;
  degraded_sources: number;
  blocked_sources: number;
  broken_sources: number;
  experimental_sources: number;
  disabled_sources: number;
}
