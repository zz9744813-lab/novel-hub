import { useEffect, useState, useMemo } from "react";
import { api, OutlineNode } from "../api";
import { Upload, Loader2, GitGraph, Target, Link2 } from "lucide-react";
import clsx from "clsx";

export function OutlineGraph({ bookId }: { bookId: string }) {
  const [nodes, setNodes] = useState<OutlineNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [outline, setOutline] = useState("");
  const [parsing, setParsing] = useState(false);
  const [result, setResult] = useState<{ type: "success" | "error"; msg: string } | null>(null);

  const fetchGraph = async () => {
    setLoading(true);
    try {
      const { nodes } = await api.outlines.graph(bookId);
      setNodes(nodes);
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
        setResult({ type: "success", msg: `解析成功 · ${r.status}` });
        fetchGraph();
      } else {
        setResult({ type: "error", msg: r.errors?.join("; ") || r.status });
      }
    } catch (e: any) {
      setResult({ type: "error", msg: e.message });
    }
    setParsing(false);
  };

  // 按依赖层级排序节点，并对同一 chapter_no 去重（保留最后一个）
  const sortedNodes = useMemo(() => {
    if (!nodes.length) return [];
    // 按 chapter_no 去重，保留最新的
    const byChapter = new Map<number, OutlineNode>();
    for (const n of nodes) {
      byChapter.set(n.chapter_no, n); // 后出现的覆盖前面的（假设是更新版本）
    }
    const unique = Array.from(byChapter.values());
    const levels = new Map<number, number>();
    const getLevel = (n: OutlineNode): number => {
      if (levels.has(n.chapter_no)) return levels.get(n.chapter_no)!;
      if (!n.depends_on || n.depends_on.length === 0) {
        levels.set(n.chapter_no, 0);
        return 0;
      }
      const maxDep = Math.max(...n.depends_on.map((d: any) => {
        const depChNo = parseInt(d.node_id?.replace("ch", "") || "0");
        const depNode = unique.find(x => x.chapter_no === depChNo);
        return depNode ? getLevel(depNode) : 0;
      }));
      levels.set(n.chapter_no, maxDep + 1);
      return maxDep + 1;
    };
    unique.forEach(n => getLevel(n));
    return [...unique].sort((a, b) => (levels.get(a.chapter_no) || 0) - (levels.get(b.chapter_no) || 0));
  }, [nodes]);

  return (
    <div className="max-w-5xl mx-auto">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-ink-200 font-serif flex items-center gap-2">
          <GitGraph size={24} className="text-accent" />
          大纲依赖图
        </h2>
        <p className="text-sm text-ink-500 mt-1">AI 解析细纲文本，构建章节 DAG</p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 size={24} className="animate-spin text-ink-600" />
        </div>
      ) : nodes.length === 0 ? (
        <div className="space-y-4">
          <div className="p-6 bg-ink-900 rounded-xl border border-ink-800">
            <label className="flex items-center gap-2 text-sm text-ink-300 mb-3">
              <Upload size={14} className="text-accent" />
              输入细纲文本，AI 将解析为结构化 DAG
            </label>
            <textarea
              value={outline}
              onChange={(e) => setOutline(e.target.value)}
              placeholder={"示例：\n第一卷\n第1章：主角出场，建立故事起点\n第2章：冲突爆发\n第3章：主角觉醒，获得能力或线索\n..."}
              className="w-full h-56 p-4 bg-ink-950 border border-ink-800 rounded-lg text-sm font-mono text-ink-300 resize-none focus-accent transition-all"
            />
            <div className="flex items-center gap-3 mt-4">
              <button
                onClick={handleParse}
                disabled={parsing || !outline.trim()}
                className="flex items-center gap-2 px-5 py-2 rounded-lg bg-accent text-ink-950 text-sm font-medium hover:bg-accent-bright disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              >
                {parsing ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
                {parsing ? "AI 解析中..." : "开始解析"}
              </button>
              {result && (
                <span className={clsx(
                  "text-sm",
                  result.type === "success" ? "text-sage" : "text-red-400"
                )}>
                  {result.msg}
                </span>
              )}
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* 概览统计 */}
          <div className="flex gap-3 mb-6">
            <div className="px-4 py-3 bg-ink-900 rounded-lg border border-ink-800 flex-1">
              <div className="text-2xl font-bold text-accent">{nodes.length}</div>
              <div className="text-xs text-ink-500 mt-1">章节节点</div>
            </div>
            <div className="px-4 py-3 bg-ink-900 rounded-lg border border-ink-800 flex-1">
              <div className="text-2xl font-bold text-ink-300">
                {nodes.filter(n => n.depends_on && n.depends_on.length > 0).length}
              </div>
              <div className="text-xs text-ink-500 mt-1">依赖关系</div>
            </div>
            <div className="px-4 py-3 bg-ink-900 rounded-lg border border-ink-800 flex-1">
              <div className="text-2xl font-bold text-ink-300">
                {new Set(nodes.flatMap(n => n.required_beats || [])).size}
              </div>
              <div className="text-xs text-ink-500 mt-1">叙事节拍</div>
            </div>
          </div>

          {/* DAG 可视化（分层节点列表） */}
          <div className="space-y-3">
            {sortedNodes.map((n) => {
              const hasDeps = n.depends_on && n.depends_on.length > 0;
              return (
                <div key={n.node_id} className="card-hover p-4 bg-ink-900 rounded-xl border border-ink-800">
                  <div className="flex items-start gap-3">
                    {/* Chapter badge */}
                    <div className="flex-shrink-0 w-12 h-12 rounded-lg bg-ink-850 border border-ink-700 flex flex-col items-center justify-center">
                      <div className="text-[9px] text-ink-500 font-mono">CH</div>
                      <div className="text-lg font-bold text-accent leading-none">{n.chapter_no}</div>
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <h4 className="font-medium text-ink-200 text-sm">
                          {n.title || `(第${n.chapter_no}章 · 待定标题)`}
                        </h4>
                      </div>

                      {/* Goal */}
                      <div className="flex items-start gap-1.5 mb-2">
                        <Target size={12} className="text-ink-500 mt-0.5 flex-shrink-0" />
                        <p className="text-xs text-ink-400 leading-relaxed">{n.goal}</p>
                      </div>

                      {/* Beats */}
                      {n.required_beats && n.required_beats.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mb-2">
                          {n.required_beats.map((beat, i) => (
                            <span key={i} className="badge bg-ink-850 text-ink-400 border border-ink-800">
                              {beat}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Dependencies */}
                      {hasDeps && (
                        <div className="flex items-center gap-1.5 text-xs text-ink-500">
                          <Link2 size={12} className="text-accent-dim" />
                          依赖 {n.depends_on.length} 个前置节点
                          <span className="text-ink-600">
                            ({n.depends_on.map((d: any) => d.node_id || "").filter(Boolean).join(", ")})
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Re-parse section */}
          <details className="mt-6">
            <summary className="cursor-pointer text-sm text-ink-500 hover:text-ink-300">
              重新解析大纲
            </summary>
            <div className="mt-3 p-4 bg-ink-900 rounded-lg border border-ink-800">
              <textarea
                value={outline}
                onChange={(e) => setOutline(e.target.value)}
                placeholder="输入新的细纲文本..."
                className="w-full h-32 p-3 bg-ink-950 border border-ink-800 rounded-lg text-sm font-mono text-ink-300 resize-none focus-accent"
              />
              <button
                onClick={handleParse}
                disabled={parsing || !outline.trim()}
                className="mt-2 flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-ink-950 text-sm font-medium disabled:opacity-40"
              >
                {parsing ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
                重新解析
              </button>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
