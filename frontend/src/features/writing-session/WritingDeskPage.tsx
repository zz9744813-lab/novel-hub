import { useCallback, useEffect, useState } from "react";
import { api, SessionChapterRun, WritingSessionView } from "../../api";
import { Activity, Cpu, Loader2, RefreshCw } from "lucide-react";
import { WritingSessionStatusCard } from "./WritingSessionStatusCard";
import { WritingSessionHistory } from "./WritingSessionHistory";

const RUN_STATUS: Record<string, { label: string; cls: string; dot: string }> = {
  queued: { label: "等待中", cls: "text-text-tertiary", dot: "bg-text-disabled" },
  running: { label: "写句中", cls: "text-brand-accent", dot: "bg-brand-accent" },
  retryable: { label: "可重试", cls: "text-amber-400", dot: "bg-amber-400" },
  succeeded: { label: "已完成", cls: "text-emerald-400", dot: "bg-emerald-400" },
  needs_human: { label: "需人工", cls: "text-red-400", dot: "bg-red-400" },
  failed: { label: "失败", cls: "text-red-400", dot: "bg-red-400" },
  paused: { label: "已暂停", cls: "text-text-secondary", dot: "bg-text-disabled" },
};

const EDITORIAL_LABEL: Record<string, string> = {
  pending_review: "待审核",
  in_review: "审核中",
  revision_requested: "退回修订",
  revising: "修订中",
  awaiting_recheck: "待复审",
  accepted: "已接受",
  accepted_with_notes: "带意见接受",
  rejected: "已拒绝",
};

function fmtTime(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function WritingDeskPage({ bookId }: { bookId: string }) {
  const [session, setSession] = useState<WritingSessionView | null>(null);
  const [chapters, setChapters] = useState<SessionChapterRun[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const pullSession = useCallback(async () => {
    try {
      const r = await api.writingSessions.current(bookId);
      setSession(r.session);
      if (r.session?.id) {
        const c = await api.writingSessions.chapters(r.session.id);
        setChapters(c.items || []);
      } else {
        setChapters([]);
      }
    } catch {
      setSession(null);
    }
  }, [bookId]);

  useEffect(() => {
    pullSession();
    const t1 = setInterval(pullSession, 10000);
    return () => clearInterval(t1);
  }, [pullSession]);

  useEffect(() => {
    let cancelled = false;
    const pullHealth = async () => {
      try {
        const h = await api.modelCenter.overview();
        if (!cancelled) setHealth(h);
      } catch {
        /* optional */
      }
    };
    pullHealth();
    const t2 = setInterval(pullHealth, 30000);
    return () => {
      cancelled = true;
      clearInterval(t2);
    };
  }, []);

  const runAction = async (action: (id: string) => Promise<any>) => {
    if (!session?.id) return;
    setBusy(true);
    setErr(null);
    try {
      await action(session.id);
      await pullSession();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const roles = (session?.model_preflight_detail?.roles || null) as Record<string, { primary?: any; fallbacks?: any[] } | undefined> | null;
  const roleNames: Record<string, string> = {
    chapter_planner: "ChapterPlanner",
    draft_writer: "DraftWriter",
    review_agent: "ReviewAgent",
    state_extractor: "StateExtractor",
    style_analyzer: "StyleAnalyzer",
  };

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity size={16} className="text-brand-accent" />
          <h1 className="text-sm text-text-primary">自动写作监控台</h1>
          <span className="text-2xs text-text-tertiary">每次会话 · 实时进度 · 模型状态</span>
        </div>
        <div className="flex items-center gap-2">
          {busy && <Loader2 size={14} className="animate-spin text-text-tertiary" />}
          <button onClick={pullSession} className="btn-ghost text-2xs px-2 py-1 rounded flex items-center gap-1">
            <RefreshCw size={11} /> 刷新
          </button>
        </div>
      </div>

      {err && <div className="text-xs text-red-400">{err}</div>}

      {session ? (
        <>
          <WritingSessionStatusCard
            session={session}
            busy={busy}
            onPause={() => runAction((id) => api.writingSessions.pause(id))}
            onResume={() => runAction((id) => api.writingSessions.resume(id))}
            onCancel={() => {
              if (window.confirm("结束本次自动写作？当前章节会先安全完成。")) {
                runAction((id) => api.writingSessions.cancel(id));
              }
            }}
            onExtend={() => runAction((id) => api.writingSessions.extend(id, 120))}
            onOpenEditorial={() => undefined}
          />

          {/* 本次会话章节进度 */}
          <div className="panel p-3">
            <div className="text-2xs text-text-disabled mb-2">
              本次会话章节进度 · {chapters.length} 轮
            </div>
            <div className="space-y-1">
              {chapters.map((c) => {
                const st = RUN_STATUS[c.status] || { label: c.status, cls: "text-text-tertiary", dot: "bg-text-disabled" };
                return (
                  <div key={c.run_id} className="flex items-center gap-3 text-xs py-1 border-b border-border-subtle last:border-0 flex-wrap">
                    <span className="font-mono text-text-secondary w-16 shrink-0">第{c.chapter_no}章</span>
                    <span className={`inline-flex items-center gap-1.5 w-20 shrink-0 ${st.cls}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                      {st.label}
                    </span>
                    {c.current_step && (
                      <span className="text-text-tertiary text-2xs w-28 truncate shrink-0">{c.current_step}</span>
                    )}
                    {c.words != null && (
                      <span className="font-mono text-text-tertiary w-24 text-right shrink-0">{c.words.toLocaleString()} 字</span>
                    )}
                    {c.editorial_status && (
                      <span className="text-2xs text-text-secondary shrink-0">
                        {EDITORIAL_LABEL[c.editorial_status] || c.editorial_status}
                      </span>
                    )}
                    <span className="text-2xs text-text-disabled ml-auto">{fmtTime(c.finished_at || c.created_at)}</span>
                  </div>
                );
              })}
              {!chapters.length && (
                <div className="text-2xs text-text-tertiary">会话刚创建，等待第一章启动…</div>
              )}
            </div>
          </div>
        </>
      ) : (
        <div className="panel p-6 text-center space-y-2">
          <div className="text-sm text-text-primary">暂无自动写作会话</div>
          <p className="text-2xs text-text-tertiary">
            从作品首页点击「开始自动写作」启动会话，这里将实时展示进度、章节时间线与模型健康状况。
          </p>
        </div>
      )}

      {/* 模型健康迷你面板 */}
      <div className="panel p-3 space-y-2">
        <div className="flex items-center gap-2">
          <Cpu size={13} className="text-brand-accent" />
          <span className="text-2xs text-text-disabled">模型健康</span>
          <span className="text-xs text-text-primary ml-auto">
            {health ? `${health.healthy ?? 0} / ${health.models ?? 0} Healthy` : "—"}
          </span>
        </div>
        {roles && (
          <div className="flex flex-wrap gap-3">
            {Object.keys(roleNames).map((role) => {
              const assign = roles[role];
              if (!assign || (!assign.primary?.model && !(assign.fallbacks || []).length)) return null;
              return (
                <div key={role} className="text-2xs text-text-secondary">
                  <div className="text-text-disabled">{roleNames[role]}</div>
                  <div className="flex items-center gap-1.5 mt-0.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                    {assign.primary?.model || "—"}
                  </div>
                  {(assign?.fallbacks || []).slice(0, 1).map((f: any, i: number) => (
                    <div key={i} className="flex items-center gap-1.5 mt-0.5 text-text-tertiary">
                      <span className="w-1.5 h-1.5 rounded-full bg-bg-hover" />
                      备用 {f.model}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
        {!roles && (
          <div className="text-2xs text-text-tertiary">模型详情与路由分配见「模型中心」</div>
        )}
      </div>

      <WritingSessionHistory bookId={bookId} />
    </div>
  );
}
