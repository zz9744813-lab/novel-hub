import { useEffect, useState, useMemo, useRef } from "react";
import { api, OutlineNode } from "../api";
import { Upload, Loader2, GitGraph, Target, Link2, FileText, CheckCircle2, AlertTriangle } from "lucide-react";
import clsx from "clsx";

export function OutlineGraph({ bookId }: { bookId: string }) {
  const [nodes, setNodes] = useState<OutlineNode[]>([]);
  const [outlineVersionId, setOutlineVersionId] = useState<string | null>(null);
  const [outlineVersion, setOutlineVersion] = useState<number | null>(null);
  const [outlineStatus, setOutlineStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [outline, setOutline] = useState("");
  const [parsing, setParsing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [result, setResult] = useState<{ type: "success" | "error"; msg: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const fetchGraph = async () => {
    setLoading(true);
    try {
      const data = await api.outlines.graph(bookId);
      setNodes(data.nodes);
      setOutlineVersionId(data.outline_version_id);
      setOutlineVersion(data.version);
      setOutlineStatus(data.status);
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  useEffect(() => { fetchGraph(); }, [bookId]);

  const handleParse = async () => {
    if (!outline.trim()) return;
    setParsing(true); setResult(null);
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
    setUploading(true); setResult(null);
    try {
      const r = await api.outlines.upload(bookId, file);
      if (r.status === "parsed") {
        setResult({ type: "success", msg: `文件「${r.filename}」解析成功 · ${r.chars} 字 · v${r.version}` });
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
    setApproving(true); setResult(null);
    try {
      const r = await api.outlines.approve(bookId, outlineVersion);
      setResult({ type: "success", msg: `大纲 v${outlineVersion} 已批准 · ${r.status}` });
      fetchGraph();
    } catch (e: any) {
      setResult({ type: "error", msg: e.message });
    }
    setApproving(false);
  };

  const sortedNodes = useMemo(() => {
    if (!nodes.length) return [];
    const levels = new Map<number, number>();
    const getLevel = (n: OutlineNode): number => {
      if (levels.has(n.chapter_no)) return levels.get(n.chapter_no)!;
      if (!n.depends_on || n.depends_on.length === 0) {
        levels.set(n.chapter_no, 0);
        return 0;
      }
      const maxDep = Math.max(...n.depends_on.map((d: any) => {
        const depChNo = parseInt(d.node_id?.replace("ch", "") || "0");
        const depNode = nodes.find(x => x.chapter_no === depChNo);
        return depNode ? getLevel(depNode) : 0;
      }));
      levels.set(n.chapter_no, maxDep + 1);
      return maxDep + 1;
    };
    nodes.forEach(n => getLevel(n));
    return [...nodes].sort((a, b) => (levels.get(a.chapter_no) || 0) - (levels.get(b.chapter_no) || 0));
  }, [nodes]);

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-2 mb-4">
        <GitGraph size={14} className="text-text-disabled" />
        <h2 className="text-xs text-text-primary uppercase tracking-wider" style={{ fontWeight: 510 }}>大纲依赖图</h2>
        <span className="text-2xs text-text-disabled">上传文件或粘贴文本，AI 解析为结构化 DAG</span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 size={18} className="animate-spin text-text-disabled" />
        </div>
      ) : nodes.length === 0 ? (
        <div className="space-y-3">
          {/* Upload zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={clsx(
              "panel p-6 border-dashed border cursor-pointer text-center transition-all duration-200",
              dragOver ? "border-brand bg-brand-muted" : "border-border-standard hover:border-text-disabled"
            )}
          >
            <input ref={fileInputRef} type="file" accept=".txt,.md,.markdown,.text" onChange={handleFileSelect} className="hidden" />
            {uploading ? (
              <Loader2 size={20} className="animate-spin text-brand-accent mx-auto mb-2" />
            ) : (
              <Upload size={20} className="text-text-disabled mx-auto mb-2" />
            )}
            <p className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>{uploading ? "上传解析中..." : "点击或拖拽文件到此处"}</p>
            <p className="text-2xs text-text-disabled mt-1">.txt / .md · max 2MB</p>
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-border" />
            <span className="text-2xs text-text-disabled">或手动粘贴</span>
            <div className="flex-1 h-px bg-border" />
          </div>

          {/* Text input */}
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
          {/* Stats row */}
          <div className="grid grid-cols-4 gap-2 mb-4">
            <div className="stat-card">
              <div className="stat-label"><GitGraph size={11} /> 节点</div>
              <div className="stat-value text-brand-accent">{nodes.length}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label"><Link2 size={11} /> 依赖</div>
              <div className="stat-value">{nodes.filter(n => n.depends_on && n.depends_on.length > 0).length}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label"><Target size={11} /> 节拍</div>
              <div className="stat-value">{new Set(nodes.flatMap(n => n.required_beats || [])).size}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">
                {outlineStatus === "approved" ? <CheckCircle2 size={11} className="text-success" /> : <AlertTriangle size={11} className="text-warning" />}
                状态
              </div>
              <div className={clsx("stat-value text-sm", outlineStatus === "approved" ? "text-success" : "text-warning")}>
                {outlineStatus === "approved" ? "已批准" : outlineStatus || "未知"}
              </div>
              <div className="text-2xs text-text-disabled font-mono mt-0.5">v{outlineVersion || "?"}</div>
            </div>
          </div>

          {/* Approve banner */}
          {outlineStatus !== "approved" && (
            <div className="flex items-center gap-2 mb-3 p-2.5 bg-warning-muted border border-warning/30 rounded-md text-xs">
              <AlertTriangle size={13} className="text-warning shrink-0" />
              <span className="text-text-secondary flex-1">大纲尚未批准，需通过 DAG 校验后方可用于章节生成</span>
              <button
                onClick={handleApprove}
                disabled={approving}
                className="btn text-2xs shrink-0"
                style={{ background: "rgba(212,162,78,0.15)", color: "#d4a24e", borderColor: "rgba(212,162,78,0.25)" }}
              >
                {approving ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle2 size={11} />}
                {approving ? "校验中..." : "批准大纲"}
              </button>
            </div>
          )}

          {result && (
            <div className={clsx(
              "mb-3 p-2.5 rounded-md text-2xs",
              result.type === "success" ? "bg-success-muted text-success border border-success/30" : "bg-danger-muted text-danger border border-danger/30"
            )}>
              {result.msg}
            </div>
          )}

          {/* DAG node list */}
          <div className="space-y-1">
            {sortedNodes.map((n) => {
              const hasDeps = n.depends_on && n.depends_on.length > 0;
              return (
                <div key={n.node_id} className="row-item items-start">
                  <div className="shrink-0 w-9 h-9 rounded bg-bg-canvas border border-border-standard flex flex-col items-center justify-center">
                    <div className="text-2xs text-text-disabled font-mono leading-none">CH</div>
                    <div className="text-xs font-bold text-brand-accent leading-none font-mono">{n.chapter_no}</div>
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-text-primary" style={{ fontWeight: 510 }}>{n.title || `(第${n.chapter_no}章 · 待定标题)`}</div>

                    <div className="flex items-start gap-1 mt-0.5">
                      <Target size={10} className="text-text-disabled mt-0.5 shrink-0" />
                      <p className="text-2xs text-text-tertiary leading-relaxed">{n.goal}</p>
                    </div>

                    {n.required_beats && n.required_beats.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {n.required_beats.map((beat, i) => (
                          <span key={i} className="badge bg-bg-surface text-text-tertiary border-border-standard text-2xs">
                            {beat}
                          </span>
                        ))}
                      </div>
                    )}

                    {hasDeps && (
                      <div className="flex items-center gap-1 text-2xs text-text-disabled mt-1">
                        <Link2 size={10} className="text-brand" />
                        dep {n.depends_on.length}
                        <span className="text-text-disabled">({n.depends_on.map((d: any) => d.node_id || "").filter(Boolean).join(", ")})</span>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Re-upload */}
          <details className="mt-4">
            <summary className="cursor-pointer text-2xs text-text-disabled hover:text-text-secondary select-none">
              重新导入大纲
            </summary>
            <div className="mt-2 space-y-2">
              <div
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
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
