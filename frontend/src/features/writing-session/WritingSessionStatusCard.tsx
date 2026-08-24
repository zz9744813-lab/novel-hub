import { AlertTriangle, Clock, Loader2, Pause, Play, Square } from "lucide-react";
import type { WritingSessionView } from "../../api";

function fmtDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 60000));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}` : `${m} 分钟`;
}

const STOP_REASON_LABELS: Record<string, string> = {
  needs_human: "需要人工处理",
  causal_hard_failure: "因果编译硬失败（CCNE Hard Block）",
  resource_blocked: "资源硬阻断",
  quality_drop: "最近首轮良品率低于阈值",
  consecutive_bad_reviews: "连续首轮退回",
  deadline: "时间窗口结束",
  outline_exhausted: "大纲已写满",
  outline_node_missing: "章纲节点缺失",
  chapter_run_failed: "章节运行失败",
};

export function WritingSessionStatusCard({
  session,
  onPause,
  onResume,
  onCancel,
  onExtend,
  onOpenEditorial,
  busy,
}: {
  session: WritingSessionView;
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
  onExtend: () => void;
  onOpenEditorial: () => void;
  busy: boolean;
}) {
  const s = session;
  const runningLike = ["running", "pausing", "paused"].includes(s.status) ||
    s.control_requested === "pause" || s.control_requested === "cancel";

  const progress = (() => {
    if (!s.started_at) return null;
    const started = new Date(s.started_at).getTime();
    const deadline = s.deadline_at ? new Date(s.deadline_at).getTime() : null;
    const now = Date.now();
    if (deadline && deadline > started) {
      return `${fmtDuration(deadline - now)} / ${fmtDuration(deadline - started)}`;
    }
    return fmtDuration(now - started);
  })();

  const row = (label: string, value: string, accent?: string) => (
    <div className="flex items-center justify-between text-xs">
      <span className="text-text-tertiary">{label}</span>
      <span className={accent || "text-text-primary"}>{value}</span>
    </div>
  );

  return (
    <div className="panel-elevated rounded-xl p-4 space-y-3">
      <div className="flex items-center gap-2">
        {s.status === "blocked" ? (
          <AlertTriangle size={15} className="text-amber-400" />
        ) : s.status === "waiting_editorial" ? (
          <Clock size={15} className="text-sky-400" />
        ) : (
          <Play size={15} className="text-brand-accent" />
        )}
        <span className="text-sm text-text-primary">
          {s.status === "blocked"
            ? "⚠ 自动写作暂停"
            : s.status === "waiting_editorial"
            ? "⏸ 等待人工审核"
            : s.control_requested === "cancel"
            ? "◐ 正在结束本次写作"
            : s.control_requested === "pause"
            ? "◐ 正在安全暂停"
            : s.status === "paused"
            ? "⏸ 已暂停"
            : s.status === "completed"
            ? "✓ 本次写作完成"
            : "● 自动写作中"}
        </span>
      </div>

      {runningLike && (
        <>
          {s.current_chapter_no ? (
            row("当前", `第${s.current_chapter_no}章`)
          ) : (
            row("当前", "—")
          )}
          {progress && row("运行", progress)}
          {row("本次完成", `${s.chapters_completed} 章 · ${(s.words_generated || 0).toLocaleString()} 字`)}
          {s.editorial_backlog_limit != null && (
            row(
              "人工审核",
              `${s.editorial_backlog ?? 0} / ${s.editorial_backlog_limit}`,
              (s.editorial_backlog ?? 0) >= (s.editorial_backlog_limit ?? 0) ? "text-amber-400" : undefined
            )
          )}
          {s.recent_first_pass && (
            row(
              "最近首轮良品率",
              `${s.recent_first_pass.good} / ${s.recent_first_pass.reviewed} · ${Math.round((s.recent_first_pass.rate || 0) * 100)}%`,
              (s.recent_first_pass.rate || 0) < 0.7 ? "text-amber-400" : "text-emerald-400"
            )
          )}

          {s.control_requested === "pause" && (
            <div className="text-2xs text-text-tertiary">当前章节完成后停止续写</div>
          )}
          {s.control_requested === "cancel" && (
            <div className="text-2xs text-text-tertiary">当前章节完成后结束</div>
          )}

          <div className="flex flex-wrap gap-2 pt-1 border-t border-border/50">
            {s.control_requested === "pause" || s.control_requested === "cancel" ? (
              <span className="text-2xs text-text-tertiary flex items-center gap-1">
                <Loader2 size={11} className="animate-spin" /> 等待当前章节完成……
              </span>
            ) : s.status === "paused" ? (
              <button onClick={onResume} disabled={busy} className="btn text-xs py-1.5 px-3 flex items-center gap-1">
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} 恢复
              </button>
            ) : (
              <button onClick={onPause} disabled={busy} className="btn text-xs py-1.5 px-3 flex items-center gap-1">
                {busy ? <Loader2 size={12} className="animate-spin" /> : <Pause size={12} />} 暂停
              </button>
            )}
            {(s.control_requested === "none" || s.status === "paused") && (
              <button onClick={onExtend} disabled={busy} className="btn text-xs py-1.5 px-3">
                延长2小时
              </button>
            )}
            <button onClick={onCancel} disabled={busy} className="btn text-xs py-1.5 px-3 text-red-400 hover:border-red-400/40 flex items-center gap-1" title="手动停止：当前章节安全完成后结束本次写作">
              <Square size={11} /> 手动停止
            </button>
          </div>
        </>
      )}

      {s.status === "waiting_editorial" && (
        <div className="space-y-2">
          <p className="text-2xs text-text-tertiary">
            未完成审核闭环：{s.editorial_backlog ?? 0} / {s.editorial_backlog_limit ?? 0}
          </p>
          <div className="flex gap-2 flex-wrap">
            <button onClick={onOpenEditorial} className="btn text-xs py-1.5 px-3">去人工审核</button>
            <button onClick={onCancel} disabled={busy} className="btn text-xs py-1.5 px-3 text-red-400 hover:border-red-400/40 flex items-center gap-1" title="手动停止：结束本次写作">
              <Square size={11} /> 手动停止
            </button>
          </div>
        </div>
      )}

      {s.status === "blocked" && (
        <div className="space-y-2 text-xs">
          <div className="text-2xs text-text-secondary">
            原因：{STOP_REASON_LABELS[s.stop_reason || ""] || s.stop_reason || "—"}
            {s.stop_detail?.chapter_no != null && ` · 第${s.stop_detail.chapter_no}章`}
            {s.stop_detail?.error_code ? ` · ${s.stop_detail.error_code}` : ""}
          </div>
          <div className="flex gap-2">
            {s.stop_reason === "needs_human" || s.stop_reason === "outline_node_missing" ? (
              <button onClick={onOpenEditorial} className="btn text-xs py-1.5 px-3">查看问题</button>
            ) : null}
            <button onClick={onResume} disabled={busy} className="btn text-xs py-1.5 px-3 flex items-center gap-1">
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />} 处理后恢复
            </button>
            <button onClick={onCancel} disabled={busy} className="btn text-xs py-1.5 px-3 text-red-400 hover:border-red-400/40 flex items-center gap-1" title="手动停止：结束本次写作">
              <Square size={11} /> 手动停止
            </button>
          </div>
        </div>
      )}

      {s.status === "completed" && (
        <div className="text-2xs text-text-tertiary">
          停止原因：{STOP_REASON_LABELS[s.stop_reason || ""] || s.stop_reason || "—"}
        </div>
      )}
    </div>
  );
}
