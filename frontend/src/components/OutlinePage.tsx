import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import clsx from "clsx";
import {
  AlertTriangle,
  ArrowRight,
  FileUp,
  GitGraph,
  LayoutList,
  ListChecks,
  Loader2,
  RefreshCw,
  ScrollText,
  Sparkles,
  Target,
} from "lucide-react";
import { api, type OutlineNode, type ChapterListItem } from "../api";
import {
  OutlineGraph,
  type OutlineNodeStatus,
  type OutlineViewNode,
} from "./OutlineGraph";

// 进行中的章节状态（视为"写作中"）
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

function inferStatus(ch: ChapterListItem | undefined): OutlineNodeStatus {
  if (!ch) return "draft";
  if (ch.status === "finalized") return "completed";
  if (ch.status === "failed") return "conflict";
  if (ch.status === "needs_human") return "needs_human";
  if (ACTIVE.has(ch.status)) return "writing";
  return "draft";
}

function resolveDepChapterNos(node: OutlineNode, all: OutlineNode[]): number[] {
  const raw = (node.depends_on ?? []) as any[];
  const byId = new Map(all.map((n) => [n.node_id, n.chapter_no]));
  const out: number[] = [];
  for (const dep of raw) {
    if (typeof dep === "number") out.push(dep);
    else if (typeof dep === "string") {
      const num = Number(dep);
      if (!Number.isNaN(num)) out.push(num);
      else {
        const m = byId.get(dep);
        if (m != null) out.push(m);
      }
    } else if (dep && typeof dep === "object") {
      const id = (dep as any).node_id ?? (dep as any).id;
      if (typeof id === "number") out.push(id);
      else {
        const m = byId.get(String(id));
        if (m != null) out.push(m);
      }
    }
  }
  return [...new Set(out)];
}

type ViewMode = "graph" | "list" | "text";

interface OutlinePageProps {
  bookId: string;
  onNavigate?: (tab: string) => void;
}

export function OutlinePage({ bookId, onNavigate }: OutlinePageProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<OutlineViewNode[]>([]);
  const [rawNodes, setRawNodes] = useState<OutlineNode[]>([]);
  const [version, setVersion] = useState<number | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("graph");
  const [selected, setSelected] = useState<OutlineViewNode | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!bookId) return;
    setLoading(true);
    setError(null);
    try {
      const [graph, chapters] = await Promise.all([
        api.outlines.graph(bookId).catch((e) => {
          throw e;
        }),
        api.chapters.list(bookId).catch(() => [] as ChapterListItem[]),
      ]);
      const raw = graph.nodes || [];
      setRawNodes(raw);
      setVersion(graph.version ?? null);
      setStatus(graph.status ?? null);

      const chMap = new Map((chapters || []).map((c) => [c.chapter_no, c]));
      const view = raw.map((n) => ({
        ...n,
        status: inferStatus(chMap.get(n.chapter_no)),
        word_count: chMap.get(n.chapter_no)?.word_count ?? 0,
      }));
      setNodes(view);
      setSelected((prev) => {
        if (prev && view.some((n) => n.node_id === prev.node_id)) return prev;
        return view[0] ?? null;
      });
    } catch (e: any) {
      setError(e?.message || String(e));
      setNodes([]);
      setRawNodes([]);
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleUpload = useCallback(() => {
    fileRef.current?.click();
  }, []);

  const onFilePicked = useCallback(
    async (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;
      setUploading(true);
      setError(null);
      try {
        const r = await api.outlines.upload(bookId, file, 500);
        if (r.status === "error" && r.errors?.length) {
          setError(`解析失败：${r.errors.join("；")}`);
        } else {
          await load();
        }
      } catch (err: any) {
        setError(err?.message || String(err));
      } finally {
        setUploading(false);
      }
    },
    [bookId, load]
  );

  const counts = useMemo(() => {
    const c = { total: nodes.length, completed: 0, writing: 0, needs_human: 0, draft: 0, conflict: 0 };
    for (const n of nodes) {
      c[n.status] = (c[n.status] || 0) + 1;
    }
    return c;
  }, [nodes]);

  // ── 三态渲染 ────────────────────────────────────────────
  if (loading) {
    return (
      <div className="space-y-3 p-1">
        <div className="h-5 w-40 animate-pulse rounded bg-bg-surface" />
        <div className="grid grid-cols-1 gap-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-card border border-border bg-bg-panel" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel flex flex-col items-center justify-center py-20 text-center">
        <AlertTriangle size={28} className="mb-3 text-danger" />
        <p className="mb-1 text-sm font-medium text-text-primary">大纲加载失败</p>
        <p className="mb-4 max-w-md text-xs text-text-tertiary">{error}</p>
        <button className="btn-primary" onClick={load}>
          <RefreshCw size={14} />
          重试
        </button>
      </div>
    );
  }

  if (nodes.length === 0) {
    return (
      <div className="panel flex flex-col items-center justify-center py-24 text-center">
        <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-bg-surface">
          <GitGraph size={26} className="text-text-disabled" />
        </div>
        <p className="mb-1 text-sm font-medium text-text-primary">还没有大纲</p>
        <p className="mb-6 max-w-sm text-xs text-text-tertiary">
          上传一份大纲文档，或用 AI 从零规划整本书的章节结构，然后在这里查看依赖图谱。
        </p>
        <div className="flex items-center gap-3">
          <button className="btn-primary" onClick={handleUpload} disabled={uploading}>
            {uploading ? <Loader2 size={14} className="animate-spin" /> : <FileUp size={14} />}
            上传大纲文件
          </button>
          {onNavigate && (
            <button className="btn" onClick={() => onNavigate("home")}>
              <Sparkles size={14} />
              AI 从零规划
            </button>
          )}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".txt,.md,.docx,.pdf,.rtf,.csv,.json,.html,.xml"
          className="hidden"
          onChange={onFilePicked}
        />
      </div>
    );
  }

  // ── 有数据：工具栏 + 三视图 ─────────────────────────────
  return (
    <div className="flex flex-col gap-3 p-1">
      {/* 工具栏 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2">
          <GitGraph size={15} className="text-brand-accent" />
          <h2 className="text-xs uppercase tracking-wider text-text-primary" style={{ fontWeight: 510 }}>
            大纲图谱
          </h2>
          <span className="font-mono text-2xs text-text-disabled">
            v{version ?? "–"} · {status ?? "–"}
          </span>
        </div>

        {/* 统计徽标 */}
        <div className="ml-2 hidden items-center gap-1.5 sm:flex">
          <StatDot color="#27a644" label={String(counts.completed)} title="已定稿" />
          <StatDot color="#8b8eff" label={String(counts.writing)} title="写作中" />
          <StatDot color="#d4a24e" label={String(counts.needs_human)} title="待人工" />
          <StatDot color="#e05555" label={String(counts.conflict)} title="失败" />
          <StatDot color="#7a808c" label={String(counts.draft)} title="未开写" />
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          {/* 视图切换 */}
          <div className="flex items-center rounded-control border border-border bg-bg-surface p-0.5">
            <ViewTab active={viewMode === "graph"} onClick={() => setViewMode("graph")} icon={<GitGraph size={13} />} label="图谱" />
            <ViewTab active={viewMode === "list"} onClick={() => setViewMode("list")} icon={<LayoutList size={13} />} label="列表" />
            <ViewTab active={viewMode === "text"} onClick={() => setViewMode("text")} icon={<ScrollText size={13} />} label="文本" />
          </div>
          <button className="btn-ghost rounded p-1.5" onClick={load} title="刷新">
            <RefreshCw size={14} />
          </button>
          <button className="btn text-2xs py-1.5" onClick={handleUpload} disabled={uploading}>
            {uploading ? <Loader2 size={12} className="animate-spin" /> : <FileUp size={12} />}
            重新上传
          </button>
        </div>
      </div>

      {/* 视图主体 */}
      {viewMode === "graph" && (
        <div className="flex gap-3">
          <div className="min-w-0 flex-1">
            <OutlineGraph nodes={nodes} onNodeClick={setSelected} layoutKey={bookId} />
          </div>
          {selected && <DetailPanel node={selected} raw={rawNodes} onClose={() => setSelected(null)} />}
        </div>
      )}

      {viewMode === "list" && (
        <div className="flex gap-3">
          <div className="flex w-full flex-col gap-2">
            {nodes.map((n) => (
              <OutlineRow
                key={n.node_id}
                node={n}
                active={selected?.node_id === n.node_id}
                onClick={() => setSelected(n)}
              />
            ))}
          </div>
          {selected && <DetailPanel node={selected} raw={rawNodes} onClose={() => setSelected(null)} />}
        </div>
      )}

      {viewMode === "text" && (
        <div className="panel p-5">
          {nodes.map((n) => {
            const deps = resolveDepChapterNos(n, rawNodes);
            return (
              <div key={n.node_id} className="flex gap-3 border-b border-border-subtle py-2.5 last:border-0">
                <span className="font-mono text-2xs text-text-disabled pt-0.5">第{n.chapter_no}章</span>
                <div className="min-w-0 flex-1">
                  <div className="text-body font-medium text-text-primary">{n.title || "（无标题）"}</div>
                  {n.goal && <div className="mt-0.5 text-2xs text-text-secondary">{n.goal}</div>}
                  {(n.required_beats ?? []).length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(n.required_beats ?? []).map((b, i) => (
                        <span key={i} className="rounded bg-bg-surface px-1.5 py-0.5 text-2xs text-text-tertiary">{b}</span>
                      ))}
                    </div>
                  )}
                  {deps.length > 0 && (
                    <div className="mt-1 text-2xs text-text-disabled">依赖：第{deps.join("、")}章</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── 子组件 ────────────────────────────────────────────────
function StatDot({ color, label, title }: { color: string; label: string; title: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border border-border bg-bg-surface px-2 py-0.5 text-2xs text-text-secondary"
      title={title}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}

function ViewTab({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "flex items-center gap-1 rounded px-2.5 py-1 text-2xs transition-all",
        active ? "bg-brand-muted text-brand-accent" : "text-text-tertiary hover:text-text-primary"
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function OutlineRow({
  node,
  active,
  onClick,
}: {
  node: OutlineViewNode;
  active: boolean;
  onClick: () => void;
}) {
  const meta = STATUS_META[node.status];
  return (
    <button
      onClick={onClick}
      className={clsx(
        "flex items-center gap-3 rounded-card border px-3.5 py-3 text-left transition-all",
        active
          ? "border-brand-accent bg-bg-surface shadow-glow"
          : "border-border bg-bg-panel hover:border-border-strong hover:bg-bg-surface"
      )}
    >
      <span className="font-mono text-2xs text-text-disabled">Ch.{node.chapter_no}</span>
      <span className="h-6 w-0.5 rounded-full" style={{ background: meta.dot }} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-body font-medium text-text-primary">{node.title || "（无标题）"}</div>
        {node.goal && <div className="truncate text-2xs text-text-tertiary">{node.goal}</div>}
      </div>
      <span className="inline-flex items-center gap-1 text-2xs text-text-tertiary">
        <ListChecks size={11} />
        {(node.required_beats ?? []).length}
      </span>
      <span className={clsx("w-14 text-right text-2xs", meta.text)}>{meta.label}</span>
    </button>
  );
}

// 需要访问 STATUS_META，从 OutlineGraph 导出的常量在此重新声明以保持单文件内聚
const STATUS_META: Record<OutlineNodeStatus, { label: string; dot: string; text: string }> = {
  draft: { label: "未开写", dot: "#7a808c", text: "text-text-tertiary" },
  writing: { label: "写作中", dot: "#8b8eff", text: "text-brand-accent" },
  needs_human: { label: "待人工", dot: "#d4a24e", text: "text-warning" },
  completed: { label: "已定稿", dot: "#27a644", text: "text-success" },
  conflict: { label: "失败", dot: "#e05555", text: "text-danger" },
};

function DetailPanel({
  node,
  raw,
  onClose,
}: {
  node: OutlineViewNode;
  raw: OutlineNode[];
  onClose: () => void;
}) {
  const meta = STATUS_META[node.status];
  const deps = resolveDepChapterNos(node, raw);
  return (
    <aside className="w-72 shrink-0 self-start rounded-card border border-border bg-bg-panel p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="font-mono text-2xs text-text-disabled">第{node.chapter_no}章</span>
        <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs", meta.text)}>
          <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: meta.dot }} />
          {meta.label}
        </span>
        <button className="ml-auto text-text-disabled hover:text-text-primary" onClick={onClose} title="关闭">
          <ArrowRight size={14} className="rotate-180" />
        </button>
      </div>
      <h3 className="mb-2 text-emphasis font-medium text-text-primary leading-snug">{node.title || "（无标题）"}</h3>
      {node.word_count != null && node.word_count > 0 && (
        <div className="mb-2 text-2xs text-text-tertiary">已写 {node.word_count} 字</div>
      )}
      {node.goal && (
        <div className="mb-3">
          <div className="mb-1 text-2xs font-medium uppercase tracking-wide text-text-disabled">目标</div>
          <p className="text-2xs leading-relaxed text-text-secondary">{node.goal}</p>
        </div>
      )}
      {(node.required_beats ?? []).length > 0 && (
        <div className="mb-3">
          <div className="mb-1 flex items-center gap-1 text-2xs font-medium uppercase tracking-wide text-text-disabled">
            <ListChecks size={11} />
            必备情节点
          </div>
          <ul className="space-y-1">
            {(node.required_beats ?? []).map((b, i) => (
              <li key={i} className="flex gap-1.5 text-2xs leading-relaxed text-text-secondary">
                <span className="text-text-disabled">·</span>
                {b}
              </li>
            ))}
          </ul>
        </div>
      )}
      {deps.length > 0 && (
        <div>
          <div className="mb-1 flex items-center gap-1 text-2xs font-medium uppercase tracking-wide text-text-disabled">
            <Target size={11} />
            依赖章节
          </div>
          <div className="flex flex-wrap gap-1">
            {deps.map((d) => (
              <span key={d} className="rounded bg-bg-surface px-1.5 py-0.5 font-mono text-2xs text-text-tertiary">
                第{d}章
              </span>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}
