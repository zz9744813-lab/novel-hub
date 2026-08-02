import { useEffect, useState } from "react";
import { api, getAdminToken } from "../../api";
import { Loader2, ListTodo, Upload, PenTool, AlertTriangle, CheckCircle2, Clock } from "lucide-react";

interface ImportItem {
  id: string;
  status: string;
  current_step: string | null;
  progress: number;
  created_at?: string;
  updated_at?: string;
  error_detail?: string | null;
}

interface ChapterRunItem {
  run_id: string;
  book_id?: string;
  chapter_id: string;
  chapter_no?: number;
  run_status: string;
  current_step: string | null;
  control_requested?: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  error_code?: string | null;
  error_detail?: unknown;
}

function displayDetail(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "object") {
    const detail = value as { message?: unknown };
    if (typeof detail.message === "string") return detail.message;
    try {
      return JSON.stringify(value);
    } catch {
      return "详情不可用";
    }
  }
  return String(value);
}

export function WritingTasksPage() {
  const [imports, setImports] = useState<ImportItem[]>([]);
  const [runs, setRuns] = useState<ChapterRunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      // import sessions: recent (no status filter so empty active still shows history)
      const imp = await api.imports.list({ limit: 50 });
      setImports(imp.sessions || []);
      // chapter runs: sample recent books then recent runs
      const books = await api.library.books();
      const runPromises = (books.books || []).slice(0, 8).map(async (b: any) => {
        try {
          const chs = await api.chapters.list(b.book_id);
          const list = Array.isArray(chs) ? chs : (chs as any)?.chapters || [];
          const runChunks = await Promise.all(
            list.slice(0, 5).map(async (ch: any) => {
              try {
                const runs = await (api as any).chapters.runs?.(ch.chapter_id);
                if (runs) {
                  return (Array.isArray(runs) ? runs : []).map((r: any) => ({
                    ...r,
                    run_id: r.run_id || r.id,
                    run_status: r.run_status || r.status,
                    book_id: b.book_id,
                    chapter_no: ch.chapter_no,
                  }));
                }
                // fallback fetch
                const r = await fetch(`/api/chapters/${ch.chapter_id}/runs`, {
                  headers: { Authorization: `Bearer ${getAdminToken() || ""}` },
                });
                if (!r.ok) return [];
                const data = await r.json();
                return (Array.isArray(data) ? data : data.runs || []).map((x: any) => ({
                  ...x,
                  run_id: x.run_id || x.id,
                  run_status: x.run_status || x.status,
                  book_id: b.book_id,
                  chapter_no: ch.chapter_no,
                }));
              } catch {
                return [];
              }
            })
          );
          return runChunks.flat();
        } catch {
          return [];
        }
      });
      const runLists = await Promise.all(runPromises);
      const allRuns: ChapterRunItem[] = runLists.flat().sort(
        (a, b) => (b.started_at || "").localeCompare(a.started_at || "")
      );
      setRuns(allRuns.slice(0, 50));
      // global needs-human
      try {
        const nh = await fetch("/api/chapters/needs-human", {
          headers: { Authorization: `Bearer ${getAdminToken() || ""}` },
        });
        if (nh.ok) {
          const data = await nh.json();
          const extra = (data.chapters || []).map((c: any) => ({
            run_id: c.run_id || c.chapter_id,
            book_id: c.book_id,
            chapter_id: c.chapter_id,
            chapter_no: c.chapter_no,
            run_status: "needs_human",
            current_step: c.current_step,
            error_code: c.error_code,
            error_detail: displayDetail(c.error_detail) || `${c.book_title || ""} needs_human`,
          }));
          // merge unique
          setRuns((prev) => {
            const ids = new Set(prev.map((p) => p.run_id));
            const merged = [...prev];
            for (const e of extra) {
              if (!ids.has(e.run_id)) merged.push(e);
            }
            return merged.slice(0, 60);
          });
        }
      } catch {
        /* ignore */
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const statusIcon = (s: string) => {
    if (s === "completed" || s === "finalized" || s === "preview_ready") return <CheckCircle2 size={12} className="text-emerald-400" />;
    if (s === "failed" || s === "needs_human") return <AlertTriangle size={12} className="text-amber-400" />;
    if (s === "analyzing" || s === "running" || s === "drafting") return <Loader2 size={12} className="animate-spin text-brand-accent" />;
    return <Clock size={12} className="text-text-disabled" />;
  };

  const activeImports = imports.filter((i) => !["completed", "failed"].includes(i.status));
  const activeRuns = runs.filter((r) => !["completed", "failed"].includes(r.run_status));
  const needsHumanRuns = runs.filter((r) => r.run_status === "needs_human");

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-base text-text-primary" style={{ fontWeight: 510 }}>
            写作任务
          </h1>
          <p className="text-xs text-text-tertiary mt-0.5">
            导入中 · 生成中 · 待人工 · 全局视角
          </p>
        </div>
        <button onClick={load} className="btn-ghost text-xs py-1.5 px-3 flex items-center gap-1.5">
          <Loader2 size={12} /> 刷新
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-text-tertiary text-xs gap-2">
          <Loader2 size={16} className="animate-spin" /> 加载任务队列…
        </div>
      ) : (
        <>
          <section className="panel-elevated rounded-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-border flex items-center gap-2">
              <Upload size={13} className="text-brand-accent" />
              <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>
                导入会话
              </span>
              <span className="ml-auto text-2xs font-mono text-text-disabled">{activeImports.length} 活跃</span>
            </div>
            {imports.length === 0 ? (
              <div className="p-4 text-xs text-text-disabled">暂无导入任务</div>
            ) : (
              <div className="divide-y divide-border">
                {imports.slice(0, 8).map((i) => (
                  <div key={i.id} className="px-3 py-2 flex items-center gap-3">
                    {statusIcon(i.status)}
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-text-primary truncate">{i.current_step || i.status}</div>
                      <div className="text-2xs text-text-disabled font-mono">
                        {i.status} · {Math.round(i.progress * 100)}% · {i.id.slice(0, 8)}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel-elevated rounded-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-border flex items-center gap-2">
              <PenTool size={13} className="text-brand-accent" />
              <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>
                章节生成
              </span>
              <span className="ml-auto text-2xs font-mono text-text-disabled">{activeRuns.length} 运行中</span>
            </div>
            {runs.length === 0 ? (
              <div className="p-4 text-xs text-text-disabled">暂无章节生成任务</div>
            ) : (
              <div className="divide-y divide-border">
                {runs.slice(0, 8).map((r) => (
                  <div key={String(r.run_id)} className="px-3 py-2 flex items-center gap-3">
                    {statusIcon(r.run_status)}
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-text-primary truncate">
                        Ch{r.chapter_no ?? "?"} · {displayDetail(r.current_step) || r.run_status}
                      </div>
                      <div className="text-2xs text-text-disabled font-mono">
                        {r.run_status} · {String(r.run_id).slice(0, 8)}
                        {r.error_code ? ` · ${r.error_code}` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel-elevated rounded-lg overflow-hidden">
            <div className="px-3 py-2 border-b border-border flex items-center gap-2">
              <AlertTriangle size={13} className="text-amber-400" />
              <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>
                待人工
              </span>
              <span className="ml-auto text-2xs font-mono text-text-disabled">{needsHumanRuns.length} 项</span>
            </div>
            {needsHumanRuns.length === 0 ? (
              <div className="p-4 text-xs text-text-disabled">无待人工处理项</div>
            ) : (
              <div className="divide-y divide-border">
                {needsHumanRuns.slice(0, 6).map((r) => (
                  <div key={r.run_id} className="px-3 py-2 flex items-center gap-3">
                    <AlertTriangle size={12} className="text-amber-400" />
                    <div className="flex-1 min-w-0">
                      <div className="text-xs text-text-primary truncate">Ch{r.chapter_no ?? "?"} 需要审核</div>
                      <div className="text-2xs text-text-disabled font-mono truncate">
                        {displayDetail(r.error_detail) || r.error_code || r.run_status}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <p className="text-2xs text-text-disabled text-center pt-2">
            数据来自 ImportSession + ChapterRun（按最近活动排序）
          </p>
        </>
      )}
    </div>
  );
}