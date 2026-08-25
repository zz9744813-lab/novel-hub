import { Loader2, Pause, Play, Square } from "lucide-react";
import type { WritingSessionView } from "../../api";

/** v9.6 §6–§9: sticky top control bar — stop is ALWAYS visible, above the fold. */
export function WritingSessionControlBar({
  session,
  busy,
  onPause,
  onResume,
  onCancel,
  onOpenEditorial,
}: {
  session: WritingSessionView;
  busy: boolean;
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
  onOpenEditorial: () => void;
}) {
  const s = session;
  const isPreflight = s.status === "created";
  const isBlocked = s.status === "blocked";
  const isWaiting = s.status === "waiting_editorial";
  const isPaused = s.status === "paused" && s.control_requested === "none";
  const cancelRequested = s.control_requested === "cancel";
  const pauseRequested = s.control_requested === "pause";

  const title = isPreflight
    ? "◐ 正在检测并配置模型"
    : isBlocked
    ? "⚠ 自动写作暂停"
    : isWaiting
    ? "⏸ 等待人工审核"
    : cancelRequested
    ? "◐ 正在安全停止"
    : pauseRequested
    ? "◐ 正在安全暂停"
    : isPaused
    ? "⏸ 已暂停"
    : "● 自动写作中";

  const subtitle = isPreflight
    ? "正在检查本书写作所需的主模型与备用模型"
    : cancelRequested
    ? `第${s.current_chapter_no || "—"}章完成后结束`
    : pauseRequested
    ? "当前章节完成后停止续写"
    : s.current_chapter_no
    ? `第${s.current_chapter_no}章`
    : "——";

  return (
    <div className="sticky top-0 z-20 panel-elevated rounded-xl px-4 py-3 flex flex-wrap items-center gap-3 backdrop-blur-lg">
      <div className="min-w-0 flex-1">
        <div className="text-xs text-text-primary">{title}</div>
        <div className="text-2xs text-text-tertiary mt-0.5 truncate">
          {subtitle}
          {!isPreflight && !cancelRequested && !pauseRequested && s.current_step ? ` · ${s.current_step}` : ""}
        </div>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {isWaiting && (
          <button onClick={onOpenEditorial} disabled={busy} className="btn text-xs py-1.5 px-3">
            去人工审核
          </button>
        )}
        {isBlocked && (
          <button onClick={onOpenEditorial} disabled={busy} className="btn text-xs py-1.5 px-3">
            查看问题
          </button>
        )}
        {cancelRequested || pauseRequested ? (
          <span className="text-2xs text-text-tertiary flex items-center gap-1">
            <Loader2 size={12} className="animate-spin" /> 停止中…
          </span>
        ) : isPaused ? (
          <button onClick={onResume} disabled={busy} className="btn text-xs py-2 px-4 flex items-center gap-1.5">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} 恢复
          </button>
        ) : !isPreflight && (
          <button onClick={onPause} disabled={busy} className="btn text-xs py-2 px-4 flex items-center gap-1.5">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Pause size={13} />} 暂停
          </button>
        )}
        <button
          onClick={onCancel}
          disabled={busy}
          className="btn text-xs py-2 px-4 text-red-400 hover:border-red-400/40 flex items-center gap-1.5"
          title="手动停止：结束本次写作"
        >
          <Square size={13} /> 手动停止
        </button>
      </div>
    </div>
  );
}
