import { useCallback, useEffect, useState } from "react";
import { api, SessionChapterRun, WritingSessionView } from "../../api";
import { Activity, History, Loader2, Play, X } from "lucide-react";
import { WritingSessionControlBar } from "./WritingSessionControlBar";
import { WritingSessionHistory } from "./WritingSessionHistory";

/** v9.6 §83: backend steps merged into the 8-stage pipeline stepper. */
const PIPELINE_STAGES = ["准备", "计划", "因果", "正文", "审核", "改稿", "记忆", "定稿"];
const STEP_MERGE: Record<string, string> = {
  query_planner: "准备",
  retrieval: "准备",
  context_assembler: "准备",
  chapter_planner: "计划",
  causal_compile: "因果",
  draft: "正文",
  mechanical_gate: "正文",
  review: "审核",
  patch: "改稿",
  state_extractor: "记忆",
  finalizer: "定稿",
};

const RUN_STATUS_LABEL: Record<string, string> = {
  queued: "等待中",
  running: "写作中",
  retryable: "可重试",
  succeeded: "已完成",
  needs_human: "需人工",
  failed: "失败",
  paused: "已暂停",
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
  return new Date(iso).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export function WritingDeskPage({
  bookId,
  onStartWriting,
  onOpenEditorial,
  onOpenModelSetup,
}: {
  bookId: string;
  onStartWriting: () => void;
  onOpenEditorial: () => void;
  onOpenModelSetup: () => void;
}) {
  const [session, setSession] = useState<WritingSessionView | null>(null);
  const [chapters, setChapters] = useState<SessionChapterRun[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const pull = useCallback(async () => {
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
    pull();
    const t = setInterval(pull, 10000);
    return () => clearInterval(t);
  }, [pull]);

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
    const t = setInterval(pullHealth, 30000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  const runAction = async (action: (id: string) => Promise<any>) => {
    if (!session?.id) return;
    setBusy(true);
    setErr(null);
    try {
      await action(session.id);
      await pull();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const currentStep = session?.current_chapter_no && session?.model_preflight_detail
    ? null
    : session?.current_step || null;
  const mergedStep = currentStep ? STEP_MERGE[currentStep] : null;
  const stageIndex = mergedStep ? PIPELINE_STAGES.indexOf(mergedStep) : -1;

  const recent = chapters.slice(-8);
  const healthOk = health ? `${health.healthy ?? 0} / ${health.models ?? 0}` : "—";
  const backupWarning = Object.values((session?.model_preflight_detail?.roles || null) as any || {})
    .some((a: any) => !(a?.fallbacks || []).length);

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-4">
      {session ? (
        <>
          <WritingSessionControlBar
            session={session}
            busy={busy}
            onPause={() => runAction((id) => api.writingSessions.pause(id))}
            onResume={() => runAction((id) => api.writingSessions.resume(id))}
            onCancel={() => {
              if (window.confirm("手动停止本次自动写作？")) {
                runAction((id) => api.writingSessions.cancel(id));
              }
            }}
            onOpenEditorial={onOpenEditorial}
          />

          {/* 当前章 Pipeline */}
          {session.current_chapter_no && (
            <section className="panel p-4">
              <div className="text-2xs text-text-disabled mb-2">第{session.current_chapter_no}章 Pipeline</div>
              <div className="flex flex-wrap items-center gap-1 text-2xs">
                {PIPELINE_STAGES.map((stage, i) => (
                  <span key={stage} className="flex items-center gap-1">
                    <span
                      className={`px-2 py-0.5 rounded ${
                        stageIndex > i ? "bg-success/15 text-text-primary" :
                        stageIndex < i ? "text-text-disabled" : "text-brand-accent border border-brand/40"
                      }`}
                    >
                      {stageIndex > i ? "✓ " : stageIndex === i ? "● " : "○ "}
                      {stage}
                    </span>
                    {i < PIPELINE_STAGES.length - 1 && <span className="text-text-disabled">→</span>}
                  </span>
                ))}
                {stageIndex === -1 && (
                  <span className="text-text-tertiary">（等待下一步骤…）</span>
                )}
              </div>
            </section>
          )}

          {/* 本次产出 KPI */}
          <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Kpi label="完成章节" value={String(session.chapters_completed ?? 0)} />
            <Kpi label="生成字数" value={(session.words_generated || 0).toLocaleString()} />
            <Kpi
              label="审核积压"
              value={session.editorial_backlog != null ? `${session.editorial_backlog} / ${session.editorial_backlog_limit ?? "—"}` : "—"}
            />
            <Kpi
              label="首轮良品率"
              value={session.recent_first_pass ? `${Math.round((session.recent_first_pass.rate || 0) * 100)}%` : "—"}
            />
          </section>

          {/* 最近章节 */}
          {(recent.length > 0 || session.status !== "created") && (
            <section className="panel p-3">
              <div className="text-2xs text-text-disabled mb-2">最近章节</div>
              <div className="space-y-1">
                {recent.slice().reverse().map((c) => (
                  <div key={c.run_id} className="flex items-center gap-3 text-xs py-1 border-b border-border-subtle last:border-0 flex-wrap">
                    <span className="font-mono text-text-secondary w-16 shrink-0">第{c.chapter_no}章</span>
                    <span className="text-text-primary w-14 shrink-0">{RUN_STATUS_LABEL[c.status] || c.status}</span>
                    <span className="text-2xs text-text-secondary">
                      {EDITORIAL_LABEL[c.editorial_status || ""] || c.editorial_status || ""}
                    </span>
                    <span className="font-mono text-text-tertiary">{c.words != null ? `${c.words.toLocaleString()}字` : ""}</span>
                    <span className="text-2xs text-text-disabled ml-auto">用时 {fmtTime(c.finished_at || c.created_at)}</span>
                  </div>
                ))}
                {!recent.length && (
                  <div className="text-2xs text-text-tertiary">第一章将在预检完成后启动…</div>
                )}
              </div>
            </section>
          )}

          {/* 模型摘要（非整版 Model Center） */}
          <section className="panel p-3 flex items-center gap-2 text-xs">
            <span className="text-text-secondary">模型：{healthOk} 正常</span>
            {backupWarning && (
              <span className="text-amber-400 text-2xs">⚠ 存在无合格备用模型的角色</span>
            )}
            <button
              onClick={onOpenModelSetup}
              className="text-2xs text-brand-accent border border-brand/30 rounded px-2 py-1 flex items-center gap-1"
            >
              <Activity size={11} /> 模型配置
            </button>
          </section>
        </>
      ) : (
        <div className="panel p-8 text-center space-y-3">
          <div className="text-sm text-text-primary">当前没有运行中的自动写作</div>
          <button onClick={onStartWriting} className="btn-primary text-xs py-2 px-5 inline-flex items-center gap-1.5">
            <Play size={13} /> 开始自动写作
          </button>
        </div>
      )}

      <div className="flex justify-end">
        <button onClick={() => setShowHistory((v) => !v)} className="text-2xs text-text-tertiary flex items-center gap-1">
          {showHistory ? <X size={11} /> : <History size={11} />}
          {showHistory ? "收起历史" : "查看历史会话"}
        </button>
      </div>
      {showHistory && <WritingSessionHistory bookId={bookId} />}

      {err && <div className="text-xs text-red-400">{err}</div>}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel-elevated rounded-lg px-4 py-3">
      <div className="text-2xs text-text-disabled">{label}</div>
      <div className="text-base font-mono text-text-primary mt-0.5">{value}</div>
    </div>
  );
}
