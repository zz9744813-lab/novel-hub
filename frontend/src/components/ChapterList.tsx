import { useEffect, useState } from "react";
import { api, OutlineNode, Chapter } from "../api";
import { Play, FileText, Loader2, BookOpen, CheckCircle2, XCircle, Clock, AlertTriangle, Pause, RotateCcw } from "lucide-react";
import clsx from "clsx";

const STATUS_CONFIG: Record<string, { color: string; bg: string; icon: any; label: string }> = {
  queued:            { color: "text-text-tertiary",  bg: "bg-bg-surface",         icon: Clock,         label: "排队中" },
  context_building:  { color: "text-info",           bg: "bg-info-muted",         icon: Loader2,       label: "构建上下文" },
  planning:          { color: "text-info",           bg: "bg-info-muted",         icon: Loader2,       label: "规划中" },
  drafting:          { color: "text-brand-accent",   bg: "bg-brand-muted",        icon: Loader2,       label: "写稿中" },
  reviewing:         { color: "text-warning",        bg: "bg-warning-muted",      icon: Loader2,       label: "审核中" },
  revisioning:       { color: "text-warning",        bg: "bg-warning-muted",      icon: Loader2,       label: "修订中" },
  state_extracting:  { color: "text-info",           bg: "bg-info-muted",         icon: Loader2,       label: "状态提取" },
  finalizing:        { color: "text-success",        bg: "bg-success-muted",      icon: Loader2,       label: "定稿中" },
  finalized:         { color: "text-success",        bg: "bg-success-muted",      icon: CheckCircle2,  label: "已定稿" },
  failed:            { color: "text-danger",         bg: "bg-danger-muted",       icon: XCircle,       label: "失败" },
  needs_human:       { color: "text-warning",        bg: "bg-warning-muted",      icon: AlertTriangle, label: "待人工" },
  paused:            { color: "text-text-disabled",  bg: "bg-bg-surface",         icon: Pause,         label: "已暂停" },
};

function getStatus(s?: string) {
  return STATUS_CONFIG[s || ""] || { color: "text-text-disabled", bg: "bg-bg-surface", icon: Clock, label: s || "待命" };
}

export function ChapterList({ bookId }: { bookId: string }) {
  const [nodes, setNodes] = useState<OutlineNode[]>([]);
  const [chapters, setChapters] = useState<Map<number, Chapter>>(new Map());
  const [running, setRunning] = useState<number | null>(null);
  const [content, setContent] = useState<Chapter | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.outlines.graph(bookId).then(({ nodes }) => setNodes(nodes)).catch(() => {}).finally(() => setLoading(false));
  }, [bookId]);

  const handleRun = async (no: number) => {
    setRunning(no);
    try {
      const r = await api.chapters.run(bookId, no);
      pollChapter(r.chapter_id);
    } catch (e) { console.error(e); }
    setRunning(null);
  };

  const pollChapter = async (id: string) => {
    try {
      const ch = await api.chapters.get(id);
      setChapters((prev) => new Map(prev).set(ch.chapter_no, ch));
      if (["queued", "context_building", "drafting", "planning", "reviewing", "revisioning", "state_extracting", "finalizing"].includes(ch.status)) {
        setTimeout(() => pollChapter(id), 10000);
      }
    } catch (e) { console.error(e); }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 size={18} className="animate-spin text-text-disabled" />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-2 mb-4">
        <FileText size={14} className="text-text-disabled" />
        <h2 className="text-xs text-text-primary uppercase tracking-wider" style={{ fontWeight: 510 }}>章节流水线</h2>
        <span className="text-2xs text-text-disabled">13步 Pipeline · 逐章 AI 生成 → 审核 → 定稿</span>
        {nodes.length > 0 && <span className="text-2xs text-text-disabled font-mono">{nodes.length} ch</span>}
      </div>

      {nodes.length === 0 ? (
        <div className="panel flex flex-col items-center py-16 text-text-tertiary">
          <BookOpen size={28} className="mb-3 opacity-20" />
          <p className="text-xs">请先在「大纲依赖」中解析大纲</p>
        </div>
      ) : (
        <div className="space-y-1">
          {nodes.map((n) => {
            const ch = chapters.get(n.chapter_no);
            const sc = getStatus(ch?.status);
            const StatusIcon = sc.icon;
            const isActive = ch && ["queued", "context_building", "drafting", "planning", "reviewing", "revisioning", "state_extracting", "finalizing"].includes(ch.status);

            return (
              <div
                key={n.node_id}
                className={clsx(
                  "row-item",
                  ch?.status === "finalized" && "border-success/20",
                  isActive && "border-brand/20 bg-brand-muted/50"
                )}
              >
                {/* Chapter number */}
                <div className="shrink-0 w-8 h-8 rounded bg-bg-canvas border border-border-standard flex items-center justify-center">
                  <span className="text-xs font-bold text-brand-accent font-mono">{n.chapter_no}</span>
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-text-primary" style={{ fontWeight: 510 }}>{n.title || `(第${n.chapter_no}章)`}</span>
                    {ch?.status && (
                      <span className={clsx("badge", sc.bg, sc.color, "text-2xs")}>
                        <StatusIcon size={10} className={clsx(isActive && "animate-spin")} />
                        {sc.label}
                      </span>
                    )}
                  </div>
                  <p className="text-2xs text-text-tertiary mt-0.5 truncate">{n.goal}</p>
                  {ch?.word_count ? (
                    <span className="text-2xs text-text-disabled font-mono">{ch.word_count} w</span>
                  ) : null}
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1.5 shrink-0">
                  {ch?.content && (
                    <button
                      onClick={() => setContent(ch)}
                      className="btn-ghost p-1.5 rounded"
                      data-tooltip="阅读内容"
                    >
                      <FileText size={13} />
                    </button>
                  )}
                  {(!ch || ch.status === "idle" || ch.status === "failed") && (
                    <button
                      onClick={() => handleRun(n.chapter_no)}
                      disabled={running === n.chapter_no}
                      className="btn-primary text-2xs py-1 px-2.5"
                    >
                      {running === n.chapter_no ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
                      生成
                    </button>
                  )}
                  {ch?.status === "failed" && (
                    <button
                      onClick={() => handleRun(n.chapter_no)}
                      disabled={running === n.chapter_no}
                      className="btn-ghost p-1.5 rounded"
                      data-tooltip="重试"
                    >
                      <RotateCcw size={12} />
                    </button>
                  )}
                  {isActive && (
                    <span className="flex items-center gap-1 text-2xs text-brand-accent">
                      <Loader2 size={11} className="animate-spin" />
                      处理中
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Content reader */}
      {content && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-6"
          onClick={() => setContent(null)}
        >
          <div
            className="panel-elevated max-w-2xl max-h-[85vh] overflow-hidden w-full animate-slide-up"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-5 py-3 border-b border-border">
              <div className="flex items-center gap-2">
                <span className="text-xs text-text-primary" style={{ fontWeight: 510 }}>
                  第 {content.chapter_no} 章{content.title ? ` · ${content.title}` : ""}
                </span>
                <span className="text-2xs text-text-disabled font-mono">{content.word_count} w</span>
              </div>
              <button onClick={() => setContent(null)} className="btn-ghost p-1">
                <XCircle size={14} />
              </button>
            </div>
            <div className="overflow-auto p-5 max-h-[calc(85vh-48px)]">
              <div className="whitespace-pre-wrap text-xs text-text-secondary leading-relaxed font-serif">
                {content.content || "(暂无内容)"}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
