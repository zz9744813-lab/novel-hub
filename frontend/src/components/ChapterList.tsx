import { useEffect, useState } from "react";
import { api, OutlineNode, Chapter } from "../api";
import { Play, FileText, Loader2, BookOpen, CheckCircle2, XCircle, Clock, AlertTriangle } from "lucide-react";
import clsx from "clsx";

const STATUS_CONFIG: Record<string, { color: string; bg: string; icon: any; label: string }> = {
  queued:            { color: "text-ink-400",     bg: "bg-ink-800",       icon: Clock,         label: "排队中" },
  context_building:  { color: "text-cyan-400",    bg: "bg-cyan-900/20",   icon: Loader2,       label: "构建上下文" },
  planning:          { color: "text-blue-400",    bg: "bg-blue-900/20",   icon: Loader2,       label: "规划中" },
  drafting:          { color: "text-accent",      bg: "bg-accent/10",     icon: Loader2,       label: "写稿中" },
  reviewing:         { color: "text-purple-400",  bg: "bg-purple-900/20", icon: Loader2,       label: "审核中" },
  revisioning:       { color: "text-orange-400",  bg: "bg-orange-900/20", icon: Loader2,       label: "修订中" },
  state_extracting:  { color: "text-teal-accent", bg: "bg-teal-900/20",   icon: Loader2,       label: "状态提取" },
  finalizing:        { color: "text-sage",        bg: "bg-sage/10",       icon: Loader2,       label: "定稿中" },
  finalized:         { color: "text-sage",        bg: "bg-sage/10",       icon: CheckCircle2,  label: "已定稿" },
  failed:            { color: "text-red-400",      bg: "bg-red-900/20",    icon: XCircle,       label: "失败" },
  needs_human:       { color: "text-orange-400",  bg: "bg-orange-900/20", icon: AlertTriangle, label: "待人工" },
  paused:            { color: "text-ink-500",     bg: "bg-ink-800",       icon: Clock,         label: "已暂停" },
};

function getStatus(s?: string) {
  return STATUS_CONFIG[s || ""] || { color: "text-ink-500", bg: "bg-ink-800", icon: Clock, label: s || "待命" };
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
      <div className="flex items-center justify-center py-20">
        <Loader2 size={24} className="animate-spin text-ink-600" />
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-ink-200 font-serif flex items-center gap-2">
          <FileText size={24} className="text-accent" />
          章节流水线
        </h2>
        <p className="text-sm text-ink-500 mt-1">
          13 步 Pipeline · 逐章 AI 生成 → 审核 → 定稿
          {nodes.length > 0 && <span className="ml-2 text-ink-600">· {nodes.length} 章待生成</span>}
        </p>
      </div>

      {nodes.length === 0 ? (
        <div className="flex flex-col items-center py-20">
          <BookOpen size={40} className="text-ink-600 mb-4" />
          <p className="text-sm text-ink-500">请先在「大纲依赖」中解析大纲</p>
        </div>
      ) : (
        <div className="space-y-2">
          {nodes.map((n) => {
            const ch = chapters.get(n.chapter_no);
            const sc = getStatus(ch?.status);
            const StatusIcon = sc.icon;
            const isActive = ch && ["queued", "context_building", "drafting", "planning", "reviewing", "revisioning", "state_extracting", "finalizing"].includes(ch.status);

            return (
              <div
                key={n.node_id}
                className={clsx(
                  "card-hover p-4 rounded-xl border flex items-center gap-4",
                  ch?.status === "finalized" ? "bg-ink-850 border-sage/20" :
                  isActive ? "bg-ink-850 border-accent/30" :
                  "bg-ink-900 border-ink-800"
                )}
              >
                {/* Chapter number */}
                <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-ink-850 border border-ink-700 flex items-center justify-center">
                  <span className="text-sm font-bold text-accent">{n.chapter_no}</span>
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-ink-200 text-sm">{n.title || `(第${n.chapter_no}章)`}</span>
                    {ch?.status && (
                      <span className={clsx("badge", sc.bg, sc.color)}>
                        <StatusIcon size={11} className={clsx(isActive && "animate-spin")} />
                        {sc.label}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-ink-500 mt-1 truncate">{n.goal}</p>
                  {ch?.word_count ? (
                    <span className="text-xs text-ink-600 font-mono mt-0.5">{ch.word_count} 字</span>
                  ) : null}
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 flex-shrink-0">
                  {ch?.content && (
                    <button
                      onClick={() => setContent(ch)}
                      className="p-2 rounded-lg text-ink-400 hover:bg-ink-800 hover:text-ink-200 transition-all"
                      title="阅读内容"
                    >
                      <FileText size={16} />
                    </button>
                  )}
                  {(!ch || ch.status === "idle" || ch.status === "failed") && (
                    <button
                      onClick={() => handleRun(n.chapter_no)}
                      disabled={running === n.chapter_no}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-ink-950 text-xs font-medium hover:bg-accent-bright disabled:opacity-50 transition-all"
                    >
                      {running === n.chapter_no ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                      生成
                    </button>
                  )}
                  {isActive && (
                    <span className="flex items-center gap-1 px-3 py-1.5 text-xs text-accent">
                      <Loader2 size={13} className="animate-spin" />
                      处理中
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Content reader modal */}
      {content && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-6"
          onClick={() => setContent(null)}
        >
          <div
            className="bg-ink-900 rounded-2xl max-w-2xl max-h-[85vh] overflow-hidden border border-ink-700 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-ink-800">
              <div>
                <h3 className="font-medium text-ink-200">
                  第 {content.chapter_no} 章{content.title ? ` · ${content.title}` : ""}
                </h3>
                <span className="text-xs text-ink-500 font-mono">{content.word_count} 字</span>
              </div>
              <button
                onClick={() => setContent(null)}
                className="text-ink-500 hover:text-ink-300 text-sm"
              >
                ✕
              </button>
            </div>

            {/* Content */}
            <div className="overflow-auto p-6 max-h-[calc(85vh-64px)]">
              <div className="whitespace-pre-wrap text-sm text-ink-300 leading-relaxed font-serif">
                {content.content || "(暂无内容)"}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
