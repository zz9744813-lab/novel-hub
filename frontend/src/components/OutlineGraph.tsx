import { useEffect, useState, useMemo, useRef } from "react";
import { api, OutlineNode } from "../api";
import {
  Upload,
  Loader2,
  GitGraph,
  Target,
  Link2,
  FileText,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import clsx from "clsx";

function depLabel(d: any, nodes: OutlineNode[]): string {
  if (!d) return "?";
  // Prefer resolve UUID / node_id against known nodes
  const byId = nodes.find((x) => x.node_id === d.node_id || x.node_id === d);
  if (byId) return `第${byId.chapter_no}章`;
  if (typeof d.chapter_no === "number") return `第${d.chapter_no}章`;
  // Legacy "ch1" style
  if (typeof d.node_id === "string" && /^ch\d+/i.test(d.node_id)) {
    return d.node_id.replace(/^ch/i, "第") + "章";
  }
  if (typeof d.node_id === "string") return d.node_id.slice(0, 8);
  return String(d);
}

function resolveDepChapterNo(d: any, nodes: OutlineNode[]): number {
  if (!d) return 0;
  const byId = nodes.find((x) => x.node_id === d.node_id || x.node_id === d);
  if (byId) return byId.chapter_no;
  if (typeof d.chapter_no === "number") return d.chapter_no;
  if (typeof d.node_id === "string") {
    const m = d.node_id.match(/^ch(\d+)/i);
    if (m) return parseInt(m[1], 10);
  }
  return 0;
}

export function OutlineGraph({ bookId }: { bookId: string }) {
  const [nodes, setNodes] = useState<OutlineNode[]>([]);
  const [outlineVersionId, setOutlineVersionId] = useState<string | null>(null);
  const [outlineVersion, setOutlineVersion] = useState<number | null>(null);
  const [outlineStatus, setOutlineStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [outline, setOutline] = useState("");
  const [parsing, setParsing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [result, setResult] = useState<{ type: "success" | "error"; msg: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const fetchGraph = async () => {
    if (!bookId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.outlines.graph(bookId);
      setNodes(Array.isArray(data.nodes) ? data.nodes : []);
      setOutlineVersionId(data.outline_version_id);
      setOutlineVersion(data.version);
      setOutlineStatus(data.status);
    } catch (e: any) {
      console.error(e);
      setLoadError(e?.message || String(e));
      setNodes([]);
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchGraph();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId]);

  const handleParse = async () => {
    if (!outline.trim()) return;
    setParsing(true);
    setResult(null);
    try {
      const r = await api.outlines.parse(bookId, { raw_outline: outline });
      if (r.status === "parsed") {
        setResult({ type: "success", msg: `解析成功 · v${r.version}` });
        setOutline("");
        fetchGraph();
      } else {
        setResult({ type: "error", msg: r.errors?.join("; ") || r.status });
      }
    } catch (e: any) {
      setResult({ type: "error", msg: e.message });
    }
    setParsing(false);
  };

  const handleFileUpload = async (file: File) => {
    if (!file) return;
    const validTypes = [".txt", ".md", ".markdown", ".text"];
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!validTypes.includes(ext)) {
      setResult({ type: "error", msg: `不支持的文件类型: ${ext}` });
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setResult({ type: "error", msg: "文件过大，最大支持 2MB" });
      return;
    }
    setUploading(true);
    setResult(null);
    try {
      const r = await api.outlines.upload(bookId, file);
      if (r.status === "parsed") {
        setResult({
          type: "success",
          msg: `文件「${r.filename}」解析成功 · ${r.chars} 字 · v${r.version}`,
        });
        fetchGraph();
      } else {
        setResult({ type: "error", msg: r.errors?.join("; ") || r.status });
      }
    } catch (e: any) {
      setResult({ type: "error", msg: e.message });
    }
    setUploading(false);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFileUpload(file);
    e.target.value = "";
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFileUpload(file);
  };

  const handleApprove = async () => {
    if (!outlineVersion) return;
    setApproving(true);
    setResult(null);
    try {
      const r = await api.outlines.approve(bookId, outlineVersion);
      setResult({ type: "success", msg: `大纲 v${outlineVersion} 已批准 · ${r.status}` });
      fetchGraph();
    } catch (e: any) {
      setResult({ type: "error", msg: e.message });
    }
    setApproving(false);
  };

  // Topological-ish levels using real UUID / chapter_no deps (never crash)
  const sortedNodes = useMemo(() => {
    if (!nodes.length) return [];
    try {
      const levels = new Map<number, number>();
      const visiting = new Set<number>();
      const getLevel = (n: OutlineNode): number => {
        if (levels.has(n.chapter_no)) return levels.get(n.chapter_no)!;
        if (visiting.has(n.chapter_no)) return 0; // cycle guard
        visiting.add(n.chapter_no);
        const deps = Array.isArray(n.depends_on) ? n.depends_on : [];
        if (deps.length === 0) {
          levels.set(n.chapter_no, 0);
          visiting.delete(n.chapter_no);
          return 0;
        }
        let maxDep = 0;
        for (const d of deps) {
          const depChNo = resolveDepChapterNo(d, nodes);
          const depNode = nodes.find((x) => x.chapter_no === depChNo);
          if (depNode) maxDep = Math.max(maxDep, getLevel(depNode));
        }
        const lv = maxDep + (deps.length ? 1 : 0);
        levels.set(n.chapter_no, lv);
        visiting.delete(n.chapter_no);
        return lv;
      };
      nodes.forEach((n) => getLevel(n));
      return [...nodes].sort((a, b) => {
        const la = levels.get(a.chapter_no) || 0;
        const lb = levels.get(b.chapter_no) || 0;
        if (la !== lb) return la - lb;
        return a.chapter_no - b.chapter_no;
      });
    } catch (e) {
      console.error("outline sort failed", e);
      return [...nodes].sort((a, b) => a.chapter_no - b.chapter_no);
    }
  }, [nodes]);

  const beatCount = useMemo(() => {
    try {
      return new Set(nodes.flatMap((n) => (Array.isArray(n.required_beats) ? n.required_beats : []))).size;
    } catch {
      return 0;
    }
  }, [nodes]);

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center gap-2 mb-4">
        <GitGraph size={14} className="text-text-disabled" />
        <h2 className="text-xs text-text-primary uppercase tracking-wider" style={{ fontWeight: 510 }}>
          大纲依赖图
        </h2>
        <span className="text-2xs text-text-disabled">上传文件或粘贴文本，AI 解析为结构化 DAG</span>
        <button onClick={fetchGraph} className="btn-ghost ml-auto text-2xs px-2 py-1 rounded">
          刷新
        </button>
      </div>

      {loadError && (
        <div className="mb-3 text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">
          加载失败：{loadError}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 size={18} className="animate-spin text-text-disabled" />
        </div>
      ) : nodes.length === 0 ? (
        <div className="space-y-3">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={clsx(
              "panel p-6 border-dashed border cursor-pointer text-center transition-all duration-200",
              dragOver ? "border-brand bg-brand-muted" : "border-border-standard hover:border-text-disabled"
            )}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.markdown,.text"
              onChange={handleFileSelect}
              className="hidden"
            />
            {uploading ? (
              <Loader2 size={20} className="animate-spin text-brand-accent mx-auto mb-2" />
            ) : (
              <Upload size={20} className="text-text-disabled mx-auto mb-2" />
            )}
            <p className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>
              {uploading ? "上传解析中..." : "点击或拖拽文件到此处"}
            </p>
            <p className="text-2xs text-text-disabled mt-1">.txt / .md · max 2MB</p>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-border" />
            <span className="text-2xs text-text-disabled">或手动粘贴</span>
            <div className="flex-1 h-px bg-border" />
          </div>

          <div className="panel p-4">
            <textarea
              value={outline}
              onChange={(e) => setOutline(e.target.value)}
              placeholder={"示例：\n第一卷\n第1章：主角出场，建立故事起点\n第2章：冲突爆发\n..."}
              className="input h-32 font-mono text-xs resize-none"
            />
            <div className="flex items-center gap-3 mt-3">
              <button
                onClick={handleParse}
                disabled={parsing || !outline.trim()}
                className="btn-primary text-2xs"
              >
                {parsing ? <Loader2 size={12} className="animate-spin" /> : <FileText size={12} />}
                {parsing ? "解析中..." : "解析文本"}
              </button>
              {result && (
                <span className={clsx("text-2xs", result.type === "success" ? "text-success" : "text-danger")}>
                  {result.msg}
                </span>
              )}
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
            <div className="stat-card">
              <div className="stat-label">
                <GitGraph size={11} /> 节点
              </div>
              <div className="stat-value text-brand-accent">{nodes.length}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">
                <Link2 size={11} /> 依赖
              </div>
              <div className="stat-value">
                {nodes.filter((n) => n.depends_on && n.depends_on.length > 0).length}
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-label">
                <Target size={11} /> 节拍
              </div>
              <div className="stat-value">{beatCount}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">
                {outlineStatus === "approved" ? (
                  <CheckCircle2 size={11} className="text-success" />
                ) : (
                  <AlertTriangle size={11} className="text-warning" />
                )}
                状态
              </div>
              <div
                className={clsx(
                  "stat-value text-sm",
                  outlineStatus === "approved" ? "text-success" : "text-warning"
                )}
              >
                {outlineStatus === "approved" ? "已批准" : outlineStatus || "未知"}
              </div>
              <div className="text-2xs text-text-disabled font-mono mt-0.5">
                v{outlineVersion || "?"}
                {outlineVersionId ? ` · ${outlineVersionId.slice(0, 8)}` : ""}
              </div>
            </div>
          </div>

          {outlineStatus !== "approved" && (
            <div className="flex items-center gap-2 mb-3 p-2.5 bg-warning-muted border border-warning/30 rounded-md text-xs">
              <AlertTriangle size={13} className="text-warning shrink-0" />
              <span className="text-text-secondary flex-1">
                大纲尚未批准，需通过 DAG 校验后方可用于章节生成
              </span>
              <button
                onClick={handleApprove}
                disabled={approving}
                className="btn text-2xs shrink-0"
                style={{
                  background: "rgba(212,162,78,0.15)",
                  color: "#d4a24e",
                  borderColor: "rgba(212,162,78,0.25)",
                }}
              >
                {approving ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle2 size={11} />}
                {approving ? "校验中..." : "批准大纲"}
              </button>
            </div>
          )}

          {result && (
            <div
              className={clsx(
                "mb-3 p-2.5 rounded-md text-2xs",
                result.type === "success"
                  ? "bg-success-muted text-success border border-success/30"
                  : "bg-danger-muted text-danger border border-danger/30"
              )}
            >
              {result.msg}
            </div>
          )}

          <div className="space-y-2">
            {sortedNodes.map((n) => {
              const deps = Array.isArray(n.depends_on) ? n.depends_on : [];
              const beats = Array.isArray(n.required_beats) ? n.required_beats : [];
              const hasDeps = deps.length > 0;
              return (
                <div key={n.node_id || n.chapter_no} className="row-item items-start">
                  <div className="shrink-0 w-10 h-10 rounded-md bg-bg-canvas border border-border-standard flex flex-col items-center justify-center">
                    <div className="text-2xs text-text-disabled font-mono leading-none">CH</div>
                    <div className="text-sm font-bold text-brand-accent leading-none font-mono">
                      {n.chapter_no}
                    </div>
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
                      {n.title || `第${n.chapter_no}章 · 待定标题`}
                    </div>

                    {n.goal && (
                      <div className="flex items-start gap-1.5 mt-1">
                        <Target size={11} className="text-text-disabled mt-0.5 shrink-0" />
                        <p className="text-xs text-text-tertiary leading-relaxed">{n.goal}</p>
                      </div>
                    )}

                    {beats.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mt-2">
                        {beats.map((beat, i) => (
                          <span
                            key={i}
                            className="badge bg-bg-surface text-text-tertiary border-border-standard text-2xs"
                          >
                            {beat}
                          </span>
                        ))}
                      </div>
                    )}

                    {hasDeps && (
                      <div className="flex flex-wrap items-center gap-1.5 text-2xs text-text-disabled mt-2">
                        <Link2 size={11} className="text-brand" />
                        <span>依赖</span>
                        {deps.map((d: any, i: number) => (
                          <span
                            key={i}
                            className="px-1.5 py-0.5 rounded bg-brand-muted/50 text-brand-accent border border-brand/20"
                          >
                            {depLabel(d, nodes)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <details className="mt-4">
            <summary className="cursor-pointer text-2xs text-text-disabled hover:text-text-secondary select-none">
              重新导入大纲
            </summary>
            <div className="mt-2 space-y-2">
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={clsx(
                  "panel p-4 border-dashed border cursor-pointer text-center",
                  dragOver ? "border-brand bg-brand-muted" : "border-border-standard hover:border-text-disabled"
                )}
              >
                <div className="flex items-center justify-center gap-1.5 text-2xs text-text-tertiary">
                  {uploading ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                  {uploading ? "上传解析中..." : "上传 .txt / .md 文件"}
                </div>
              </div>
              <div className="panel p-3">
                <textarea
                  value={outline}
                  onChange={(e) => setOutline(e.target.value)}
                  placeholder="输入新的细纲文本..."
                  className="input h-24 font-mono text-xs resize-none"
                />
                <button
                  onClick={handleParse}
                  disabled={parsing || !outline.trim()}
                  className="btn-primary text-2xs mt-2"
                >
                  {parsing ? <Loader2 size={11} className="animate-spin" /> : <FileText size={11} />}
                  重新解析
                </button>
              </div>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
