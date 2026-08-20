import { useEffect, useState } from "react";
import { api, OutlineNode, Chapter, ChapterListItem } from "../api";
import {
  Play,
  FileText,
  Loader2,
  BookOpen,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Pause,
  RotateCcw,
  Download,
} from "lucide-react";
import clsx from "clsx";

const STATUS_CONFIG: Record<
  string,
  { color: string; bg: string; icon: any; label: string }
> = {
  queued: { color: "text-text-tertiary", bg: "bg-bg-surface", icon: Clock, label: "排队中" },
  context_building: { color: "text-info", bg: "bg-info-muted", icon: Loader2, label: "构建上下文" },
  planning: { color: "text-info", bg: "bg-info-muted", icon: Loader2, label: "规划中" },
  drafting: { color: "text-brand-accent", bg: "bg-brand-muted", icon: Loader2, label: "写稿中" },
  reviewing: { color: "text-warning", bg: "bg-warning-muted", icon: Loader2, label: "审核中" },
  patching: { color: "text-warning", bg: "bg-warning-muted", icon: Loader2, label: "修补中" },
  revisioning: { color: "text-warning", bg: "bg-warning-muted", icon: Loader2, label: "修订中" },
  state_extracting: { color: "text-info", bg: "bg-info-muted", icon: Loader2, label: "状态提取" },
  consistency_check: { color: "text-info", bg: "bg-info-muted", icon: Loader2, label: "一致性检查" },
  finalizing: { color: "text-success", bg: "bg-success-muted", icon: Loader2, label: "定稿中" },
  finalized: { color: "text-success", bg: "bg-success-muted", icon: CheckCircle2, label: "已定稿" },
  failed: { color: "text-danger", bg: "bg-danger-muted", icon: XCircle, label: "失败" },
  needs_human: { color: "text-warning", bg: "bg-warning-muted", icon: AlertTriangle, label: "待人工" },
  paused: { color: "text-text-disabled", bg: "bg-bg-surface", icon: Pause, label: "已暂停" },
  dependency_check: { color: "text-info", bg: "bg-info-muted", icon: Loader2, label: "依赖检查" },
};

function getStatus(s?: string) {
  return (
    STATUS_CONFIG[s || ""] || {
      color: "text-text-disabled",
      bg: "bg-bg-surface",
      icon: Clock,
      label: s || "待命",
    }
  );
}

const ACTIVE = new Set([
  "queued",
  "context_building",
  "drafting",
  "planning",
  "reviewing",
  "patching",
  "revisioning",
  "state_extracting",
  "consistency_check",
  "finalizing",
  "dependency_check",
  "running",
]);

function formatNovelText(raw: string): string {
  if (!raw) return "";
  // Normalize newlines, collapse 3+ blanks, indent paragraphs for CN novel feel
  let t = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  t = t.replace(/\n{3,}/g, "\n\n").trim();
  return t
    .split("\n")
    .map((line) => {
      const s = line.trim();
      if (!s) return "";
      // Keep markdown-ish headings as-is
      if (/^#{1,3}\s/.test(s)) return s;
      return "　　" + s;
    })
    .join("\n");
}

export function ChapterList({ bookId }: { bookId: string }) {
  const [nodes, setNodes] = useState<OutlineNode[]>([]);
  const [chapters, setChapters] = useState<Map<number, Chapter>>(new Map());
  const [running, setRunning] = useState<number | null>(null);
  const [content, setContent] = useState<Chapter | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [humanDetail, setHumanDetail] = useState<any | null>(null);
  const [runDetail, setRunDetail] = useState<any | null>(null);
  const [writingNext, setWritingNext] = useState(false);

  const load = async () => {
    if (!bookId) return;
    setLoading(true);
    setError(null);
    try {
      const [graph, list] = await Promise.all([
        api.outlines.graph(bookId).catch(() => ({ nodes: [] as OutlineNode[] })),
        api.chapters.list(bookId).catch(() => [] as ChapterListItem[]),
      ]);
      setNodes(graph.nodes || []);
      const map = new Map<number, Chapter>();
      for (const item of list || []) {
        map.set(item.chapter_no, {
          chapter_id: item.chapter_id,
          chapter_no: item.chapter_no,
          status: item.status,
          title: item.title,
          content: null,
          word_count: item.word_count || 0,
          finalized_version: null,
        });
      }
      setChapters(map);
      for (const item of list || []) {
        if (item.status === "finalized" || ACTIVE.has(item.status)) {
          api.chapters
            .get(item.chapter_id)
            .then((ch) => {
              setChapters((prev) => new Map(prev).set(ch.chapter_no, ch));
              if (ACTIVE.has(ch.status)) pollChapter(ch.chapter_id);
            })
            .catch(() => {});
        }
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId]);

  const handleRun = async (no: number) => {
    setRunning(no);
    try {
      const r = await api.chapters.run(bookId, no);
      pollChapter(r.chapter_id);
      await load();
    } catch (e) {
      console.error(e);
      setError(e instanceof Error ? e.message : String(e));
    }
    setRunning(null);
  };

  const handleWriteNext = async () => {
    setWritingNext(true);
    setError(null);
    try {
      const r = await api.chapters.runNext(bookId);
      await load();
      if (r.chapter_id) pollChapter(r.chapter_id);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setWritingNext(false);
    }
  };

  const pollChapter = async (id: string) => {
    try {
      const ch = await api.chapters.get(id);
      setChapters((prev) => new Map(prev).set(ch.chapter_no, ch));
      if (ACTIVE.has(ch.status)) {
        setTimeout(() => pollChapter(id), 8000);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const openContent = async (ch: Chapter) => {
    try {
      if (!ch.content && ch.chapter_id) {
        const full = await api.chapters.get(ch.chapter_id);
        setContent(full);
        setChapters((prev) => new Map(prev).set(full.chapter_no, full));
      } else {
        setContent(ch);
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  const downloadChapter = (ch: Chapter) => {
    const text = formatNovelText(ch.content || "");
    const blob = new Blob(
      [`第${ch.chapter_no}章${ch.title ? " · " + ch.title : ""}\n\n${text}\n`],
      { type: "text/plain;charset=utf-8" }
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ch${ch.chapter_no}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const downloadBook = async () => {
    setExporting(true);
    try {
      await api.books.exportDownload(bookId);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setExporting(false);
    }
  };

  const handlePause = async (ch: Chapter) => {
    if (!ch.chapter_id) return;
    try {
      await api.chapters.pause(ch.chapter_id);
      pollChapter(ch.chapter_id);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  const handleResume = async (ch: Chapter) => {
    if (!ch.chapter_id) return;
    try {
      await api.chapters.resume(ch.chapter_id);
      pollChapter(ch.chapter_id);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  const openNeedsHuman = async (ch: Chapter) => {
    if (!ch.chapter_id) return;
    try {
      const d = await api.chapters.needsHuman(ch.chapter_id);
      setHumanDetail(d);
      setRunDetail(null);
      if (d?.run?.run_id || d?.active_run_id) {
        const rid = d.active_run_id || d.run?.run_id;
        if (rid) {
          const rd = await api.chapterRuns.get(rid);
          setRunDetail(rd);
        }
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 size={18} className="animate-spin text-text-disabled" />
      </div>
    );
  }

  const rows: Array<{
    chapter_no: number;
    title?: string | null;
    goal?: string;
    node_id?: string;
  }> =
    nodes.length > 0
      ? nodes.map((n) => ({
          chapter_no: n.chapter_no,
          title: n.title,
          goal: n.goal,
          node_id: n.node_id,
        }))
      : Array.from(chapters.values())
          .sort((a, b) => a.chapter_no - b.chapter_no)
          .map((c) => ({
            chapter_no: c.chapter_no,
            title: c.title,
            goal: "",
            node_id: c.chapter_id,
          }));

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        <FileText size={14} className="text-text-disabled" />
        <h2
          className="text-xs text-text-primary uppercase tracking-wider"
          style={{ fontWeight: 510 }}
        >
          章节流水线
        </h2>
        <span className="text-2xs text-text-disabled">逐章生成 · 审核 · 定稿</span>
        {rows.length > 0 && (
          <span className="text-2xs text-text-disabled font-mono">{rows.length} ch</span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={handleWriteNext}
            disabled={writingNext}
            className="btn-primary text-2xs py-1.5 px-3 flex items-center gap-1.5"
            title="自动写下一章（没有大纲节点会自动补一条）"
          >
            {writingNext ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
            开始写下一章
          </button>
          <button
            onClick={downloadBook}
            disabled={exporting}
            className="btn text-2xs py-1 px-2.5"
            title="下载整本 .txt"
          >
            {exporting ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />}
            下载全书
          </button>
          <button onClick={load} className="btn-ghost text-2xs px-2 py-1 rounded">
            刷新
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-3 text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <div className="panel flex flex-col items-center py-16 text-text-tertiary">
          <BookOpen size={28} className="mb-3 opacity-20" />
          <p className="text-xs">请先在「大纲依赖」中解析大纲</p>
        </div>
      ) : (
        <div className="space-y-2">
          {rows.map((n) => {
            const ch = chapters.get(n.chapter_no);
            const sc = getStatus(ch?.status);
            const StatusIcon = sc.icon;
            const isActive = !!(ch && ACTIVE.has(ch.status));
            const canRun =
              !ch ||
              ["idle", "failed", "queued", "needs_human", "paused"].includes(ch.status) ||
              ch.status === "finalized";

            return (
              <div
                key={n.node_id || n.chapter_no}
                className={clsx(
                  "row-item group/row",
                  ch?.status === "finalized" && "border-success/20",
                  isActive ? "border-brand/30 bg-brand-muted/40" : "",
                  isActive && "shadow-[inset_3px_0_8px_-3px_rgba(139,142,255,0.5)]"
                )}
              >
                <div className="shrink-0 w-9 h-9 rounded-md bg-brand-muted/60 border border-brand/20 flex items-center justify-center transition-colors duration-150 group-hover/row:bg-brand-muted group-hover/row:border-brand/30">
                  <span className="text-sm font-bold text-brand-accent font-mono">
                    {n.chapter_no}
                  </span>
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
                      {n.title || ch?.title || `第${n.chapter_no}章`}
                    </span>
                    {ch?.status && (
                      <span className={clsx("badge", sc.bg, sc.color, "text-2xs")}>
                        <StatusIcon size={10} className={clsx(isActive && "animate-spin")} />
                        {sc.label}
                      </span>
                    )}
                  </div>
                  {n.goal ? (
                    <p className="text-xs text-text-tertiary mt-0.5 truncate">{n.goal}</p>
                  ) : null}
                  {ch?.word_count ? (
                    <span className="text-2xs text-text-disabled font-mono">
                      {ch.word_count} 字
                    </span>
                  ) : null}
                </div>

                <div className="flex items-center gap-1.5 shrink-0">
                  {(ch?.status === "finalized" || ch?.content) && (
                    <>
                      <button
                        onClick={() => ch && openContent(ch)}
                        className="btn-ghost p-1.5 rounded"
                        title="阅读"
                      >
                        <FileText size={13} />
                      </button>
                      <button
                        onClick={async () => {
                          if (!ch) return;
                          let full = ch;
                          if (!ch.content) {
                            full = await api.chapters.get(ch.chapter_id);
                            setChapters((prev) => new Map(prev).set(full.chapter_no, full));
                          }
                          downloadChapter(full);
                        }}
                        className="btn-ghost p-1.5 rounded"
                        title="下载本章"
                      >
                        <Download size={13} />
                      </button>
                    </>
                  )}
                  {canRun && !isActive && (
                    <button
                      onClick={() => handleRun(n.chapter_no)}
                      disabled={running === n.chapter_no}
                      className="btn-primary text-2xs py-1 px-2.5"
                    >
                      {running === n.chapter_no ? (
                        <Loader2 size={11} className="animate-spin" />
                      ) : (
                        <Play size={11} />
                      )}
                      {ch?.status === "finalized" ? "重跑" : "生成"}
                    </button>
                  )}
                  {ch?.status === "failed" && (
                    <button
                      onClick={() => handleRun(n.chapter_no)}
                      disabled={running === n.chapter_no}
                      className="btn-ghost p-1.5 rounded"
                      title="重试"
                    >
                      <RotateCcw size={12} />
                    </button>
                  )}
                  {ch && (ch.status === "needs_human" || ch.status === "paused" || ch.status === "failed") && (
                    <button
                      onClick={() => openNeedsHuman(ch)}
                      className="btn-ghost p-1.5 rounded"
                      title="Run / 待人工详情"
                    >
                      <AlertTriangle size={12} />
                    </button>
                  )}
                  {ch && isActive && (
                    <button
                      onClick={() => handlePause(ch)}
                      className="btn-ghost p-1.5 rounded"
                      title="暂停"
                    >
                      <Pause size={12} />
                    </button>
                  )}
                  {ch && (ch.status === "paused" || ch.status === "needs_human") && (
                    <button
                      onClick={() => handleResume(ch)}
                      className="btn-ghost p-1.5 rounded"
                      title="恢复"
                    >
                      <Play size={12} />
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

      {(humanDetail || runDetail) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={() => { setHumanDetail(null); setRunDetail(null); }}>
          <div className="bg-bg-elevated border border-border rounded-xl max-w-lg w-full max-h-[80vh] overflow-auto p-4 space-y-3 animate-modal-in" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>Run / 待人工</h3>
              <button className="text-2xs text-text-disabled" onClick={() => { setHumanDetail(null); setRunDetail(null); }}>关闭</button>
            </div>
            {humanDetail && (
              <div className="space-y-1 text-2xs font-mono text-text-secondary">
                <div>chapter: {humanDetail.chapter_no} · {humanDetail.status}</div>
                <div>reason: {humanDetail.last_transition_reason || "—"}</div>
                {humanDetail.run && (
                  <div>run: {humanDetail.run.status} · step={humanDetail.run.current_step || "—"} · err={humanDetail.run.error_code || "—"}</div>
                )}
                <div className="pt-2 text-text-disabled">recent steps</div>
                <ul className="space-y-1 max-h-40 overflow-auto">
                  {(humanDetail.recent_steps || []).map((s: any, i: number) => (
                    <li key={i}>{s.step_name}/{s.step_key} · {s.status} · {s.error_code || ""}</li>
                  ))}
                </ul>
                <div className="pt-2 text-text-disabled">state events</div>
                <ul className="space-y-1 max-h-32 overflow-auto">
                  {(humanDetail.recent_state_events || []).map((e: any, i: number) => (
                    <li key={i}>{e.from_state}→{e.to_state} · {e.reason || ""}</li>
                  ))}
                </ul>
              </div>
            )}
            {runDetail && (
              <div className="space-y-1 text-2xs font-mono text-text-secondary border-t border-border pt-2">
                <div>run_status: {runDetail.run_status || runDetail.status} · chapter_status: {runDetail.chapter_status}</div>
                <div>control: {runDetail.control_requested || "none"} · lease: {runDetail.lease_owner || "—"}</div>
                <div className="text-text-disabled">steps ({(runDetail.steps || []).length})</div>
                <ul className="space-y-1 max-h-48 overflow-auto">
                  {(runDetail.steps || []).map((s: any) => (
                    <li key={s.step_run_id}>{s.step_name} · {s.status} · {s.step_key}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}

      {content && (
        <div
          className="fixed inset-0 bg-black/75 backdrop-blur-[2px] flex items-center justify-center z-50 p-4 sm:p-8"
          onClick={() => setContent(null)}
        >
          <div
            className="novel-reader panel-elevated w-full max-w-3xl max-h-[90vh] overflow-hidden flex flex-col animate-slide-up"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-3.5 border-b border-border shrink-0">
              <div className="min-w-0">
                <div className="text-sm text-text-primary truncate" style={{ fontWeight: 510 }}>
                  第 {content.chapter_no} 章
                  {content.title ? ` · ${content.title}` : ""}
                </div>
                <div className="text-2xs text-text-disabled font-mono mt-0.5">
                  {content.word_count || 0} 字 · v{content.finalized_version ?? "?"}
                </div>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => downloadChapter(content)}
                  className="btn-ghost p-1.5 rounded"
                  title="下载本章"
                >
                  <Download size={14} />
                </button>
                <button onClick={() => setContent(null)} className="btn-ghost p-1.5 rounded">
                  <XCircle size={14} />
                </button>
              </div>
            </div>
            <div className="overflow-auto flex-1 novel-reader-body">
              <article className="novel-prose">
                {formatNovelText(content.content || "(暂无内容)")
                  .split("\n")
                  .map((line, i) =>
                    line === "" ? (
                      <div key={i} className="h-4" />
                    ) : (
                      <p key={i}>{line}</p>
                    )
                  )}
              </article>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
