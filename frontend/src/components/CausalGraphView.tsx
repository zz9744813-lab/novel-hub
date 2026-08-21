import { useEffect, useMemo, useState } from "react";
import {
  api,
  CausalGraph,
  CausalGraphNode,
  CausalGraphLink,
  CounterfactualReport,
} from "../api";
import { Loader2, GitBranch, ShieldQuestion, Zap, Link2, Activity } from "lucide-react";
import clsx from "clsx";

const NODE_W = 168;
const NODE_H = 46;
const COL_GAP = 92;
const ROW_GAP = 22;

const EVENT_STYLE: Record<string, { color: string; bg: string; label: string }> = {
  decision: { color: "#8b8eff", bg: "rgba(107,122,255,0.14)", label: "决策" },
  action: { color: "#8b8eff", bg: "rgba(107,122,255,0.14)", label: "行动" },
  perception: { color: "#5ba8ef", bg: "rgba(91,168,239,0.12)", label: "感知" },
  knowledge: { color: "#5ba8ef", bg: "rgba(91,168,239,0.12)", label: "获知" },
  emotion: { color: "#d4a24e", bg: "rgba(212,162,78,0.12)", label: "情绪" },
  state_change: { color: "#27a644", bg: "rgba(39,166,68,0.12)", label: "状态" },
  relationship: { color: "#d4a574", bg: "rgba(212,165,116,0.14)", label: "关系" },
  reveal: { color: "#e05555", bg: "rgba(224,85,85,0.12)", label: "揭示" },
};

function eventStyle(type: string) {
  return EVENT_STYLE[type] || { color: "#7a808c", bg: "rgba(122,128,140,0.12)", label: type || "事件" };
}

interface PositionedNode {
  node: CausalGraphNode;
  x: number;
  y: number;
  layer: number;
}

function layeredLayout(nodes: CausalGraphNode[], links: CausalGraphLink[]) {
  const idSet = new Set(nodes.map((n) => n.id));
  const inDeg = new Map<string, number>();
  const out = new Map<string, string[]>();
  for (const n of nodes) {
    inDeg.set(n.id, 0);
    out.set(n.id, []);
  }
  for (const l of links) {
    if (!idSet.has(l.source) || !idSet.has(l.target) || l.source === l.target) continue;
    inDeg.set(l.target, (inDeg.get(l.target) || 0) + 1);
    out.get(l.source)!.push(l.target);
  }
  // Kahn layering; cycle members keep last computed layer
  const layerOf = new Map<string, number>();
  let queue = nodes.filter((n) => (inDeg.get(n.id) || 0) === 0).map((n) => n.id);
  queue.forEach((id) => layerOf.set(id, 0));
  let guard = 0;
  while (queue.length && guard++ < nodes.length * 2) {
    const next: string[] = [];
    for (const id of queue) {
      for (const t of out.get(id) || []) {
        const cand = (layerOf.get(id) || 0) + 1;
        if ((layerOf.get(t) || -1) < cand) layerOf.set(t, cand);
        const d = (inDeg.get(t) || 0) - 1;
        inDeg.set(t, d);
        if (d === 0) next.push(t);
      }
    }
    queue = next;
  }
  nodes.forEach((n) => {
    if (!layerOf.has(n.id)) layerOf.set(n.id, 0);
  });

  const byLayer = new Map<number, CausalGraphNode[]>();
  for (const n of nodes) {
    const l = layerOf.get(n.id) || 0;
    if (!byLayer.has(l)) byLayer.set(l, []);
    byLayer.get(l)!.push(n);
  }

  const maxLayer = Math.max(0, ...Array.from(byLayer.keys()));
  const layerSizes = Array.from({ length: maxLayer + 1 }, (_, l) => (byLayer.get(l) || []).length);
  const maxRows = Math.max(1, ...layerSizes);
  const canvasH = maxRows * (NODE_H + ROW_GAP) + ROW_GAP;
  const canvasW = (maxLayer + 1) * (NODE_W + COL_GAP) + COL_GAP;

  const positioned: PositionedNode[] = [];
  for (const [layer, group] of byLayer) {
    const blockH = group.length * (NODE_H + ROW_GAP) - ROW_GAP;
    const offsetY = (canvasH - blockH) / 2;
    group.forEach((node, i) => {
      positioned.push({
        node,
        x: COL_GAP + layer * (NODE_W + COL_GAP),
        y: offsetY + i * (NODE_H + ROW_GAP),
        layer,
      });
    });
  }
  positioned.sort((a, b) => a.layer - b.layer || a.y - b.y);
  return { positioned, canvasW, canvasH };
}

function edgePath(from: PositionedNode, to: PositionedNode) {
  const x1 = from.x + NODE_W;
  const y1 = from.y + NODE_H / 2;
  const x2 = to.x;
  const y2 = to.y + NODE_H / 2;
  const dx = Math.max(28, (x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

const CF_BADGE: Record<string, { label: string; cls: string }> = {
  necessary_support: { label: "必要支撑", cls: "text-danger bg-danger-muted" },
  contributing_support: { label: "贡献支撑", cls: "text-warning bg-warning-muted" },
  motivation_redundancy: { label: "动机冗余", cls: "text-warning bg-warning-muted" },
  false_causal_emphasis: { label: "虚假因果", cls: "text-danger bg-danger-muted" },
};

export function CausalGraphView({ chapterId }: { chapterId: string }) {
  const [graph, setGraph] = useState<CausalGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [audit, setAudit] = useState<CounterfactualReport | null>(null);
  const [auditing, setAuditing] = useState(false);
  const [auditErr, setAuditErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    setGraph(null);
    setAudit(null);
    api.causalGraph
      .get(chapterId)
      .then((g) => setGraph(g))
      .catch((e) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false));
  }, [chapterId]);

  const layout = useMemo(
    () => (graph ? layeredLayout(graph.nodes, graph.links) : null),
    [graph]
  );
  const posById = useMemo(() => {
    const m = new Map<string, PositionedNode>();
    layout?.positioned.forEach((p) => m.set(p.node.id, p));
    return m;
  }, [layout]);

  const connected = useMemo(() => {
    if (!hover || !graph) return null;
    const set = new Set<string>([hover]);
    for (const l of graph.links) {
      if (l.source === hover) set.add(l.target);
      if (l.target === hover) set.add(l.source);
    }
    return set;
  }, [hover, graph]);

  const runAudit = async () => {
    setAuditing(true);
    setAuditErr(null);
    try {
      const r = await api.causalGraph.audit(chapterId);
      setAudit(r.report);
    } catch (e: any) {
      setAuditErr(e?.message || String(e));
    } finally {
      setAuditing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 gap-2 text-text-tertiary text-xs">
        <Loader2 size={14} className="animate-spin" />
        正在装配因果图谱…
      </div>
    );
  }
  if (err) {
    return (
      <div className="panel flex flex-col items-center py-12 text-text-tertiary">
        <GitBranch size={24} className="mb-2 opacity-25" />
        <p className="text-xs">{err}</p>
        <p className="text-2xs text-text-disabled mt-1">该章节尚未提交因果图谱（定稿后生成）</p>
      </div>
    );
  }
  if (!graph || !layout) return null;

  if (graph.nodes.length === 0) {
    return (
      <div className="panel flex flex-col items-center py-12 text-text-tertiary">
        <GitBranch size={24} className="mb-2 opacity-25" />
        <p className="text-xs">本章没有已提交的事件</p>
        <p className="text-2xs text-text-disabled mt-1">章节定稿时，Pipeline 会把场景契约提交为因果图谱</p>
      </div>
    );
  }

  const hardCount = graph.stats.hard_edge_count;

  return (
    <div className="space-y-3">
      {/* stats + audit action */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="badge bg-bg-surface text-text-secondary text-2xs">
          <Activity size={10} /> {graph.stats.event_count} 事件
        </span>
        <span className="badge bg-bg-surface text-text-secondary text-2xs">
          <Link2 size={10} /> {graph.stats.edge_count} 因果边
        </span>
        <span className="badge bg-brand-muted text-brand-accent text-2xs">
          <Zap size={10} /> {hardCount} 硬边
        </span>
        <span className="text-2xs text-text-disabled ml-1 hidden sm:inline">
          悬停节点查看因果上下游
        </span>
        <button
          onClick={runAudit}
          disabled={auditing}
          className="btn-ghost text-2xs py-1 px-2.5 ml-auto rounded border border-border"
          title="移除关键事件并重放，检验因果支撑是否成立"
        >
          {auditing ? (
            <Loader2 size={11} className="animate-spin" />
          ) : (
            <ShieldQuestion size={11} />
          )}
          反事实审计
        </button>
      </div>

      {/* graph canvas */}
      <div className="panel overflow-x-auto" style={{ background: "rgba(0,0,0,0.18)" }}>
        <svg
          width="100%"
          viewBox={`0 0 ${layout.canvasW} ${layout.canvasH}`}
          style={{ minWidth: Math.min(layout.canvasW, 1100), display: "block" }}
          onMouseLeave={() => setHover(null)}
        >
          <defs>
            <marker id="cg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M 0 0 L 8 4 L 0 8 z" fill="rgba(139,142,255,0.75)" />
            </marker>
            <marker id="cg-arrow-soft" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M 0 0 L 8 4 L 0 8 z" fill="rgba(122,128,140,0.55)" />
            </marker>
          </defs>

          {/* edges */}
          {graph.links.map((l, i) => {
            const from = posById.get(l.source);
            const to = posById.get(l.target);
            if (!from || !to) return null;
            const isHard = l.mode === "hard";
            const lit = !hover || (connected?.has(l.source) && connected?.has(l.target));
            const dim = !!hover && !lit;
            return (
              <path
                key={`${l.source}-${l.target}-${i}`}
                d={edgePath(from, to)}
                fill="none"
                stroke={isHard ? "#8b8eff" : "#7a808c"}
                strokeWidth={isHard ? 1.8 : 1.1}
                strokeDasharray={isHard ? undefined : "4 4"}
                strokeOpacity={dim ? 0.08 : isHard ? 0.62 : 0.34}
                markerEnd={isHard ? "url(#cg-arrow)" : "url(#cg-arrow-soft)"}
                className="cg-edge"
                style={{ animationDelay: `${Math.min(900, 260 + i * 45)}ms` }}
              />
            );
          })}

          {/* nodes */}
          {layout.positioned.map((p, i) => {
            const st = eventStyle(p.node.event_type);
            const isHover = hover === p.node.id;
            const dim = !!hover && !connected?.has(p.node.id);
            return (
              <g
                key={p.node.id}
                transform={`translate(${p.x}, ${p.y})`}
                opacity={dim ? 0.28 : 1}
                style={{ transition: "opacity 0.18s ease" }}
              >
                <g
                  className="cg-node"
                  style={{ animationDelay: `${Math.min(i * 40, 700)}ms` }}
                  onMouseEnter={() => setHover(p.node.id)}
                >
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    rx={9}
                    fill={isHover ? "rgba(28,30,36,0.98)" : "rgba(28,30,36,0.92)"}
                    stroke={isHover ? st.color : "rgba(255,255,255,0.10)"}
                    strokeWidth={isHover ? 1.5 : 1}
                    style={{ filter: isHover ? `drop-shadow(0 0 10px ${st.color}55)` : undefined }}
                  />
                  <rect width={3.5} height={NODE_H} rx={1.75} fill={st.color} />
                  <text x={12} y={18} fontSize={9.5} fill={st.color} style={{ fontWeight: 590, letterSpacing: "0.04em" }}>
                    {st.label} · S{p.node.scene_id?.slice(0, 4)}
                  </text>
                  <text x={12} y={34} fontSize={10} fill="#c8cdd6">
                    {(p.node.excerpt || "（无摘录）").slice(0, 22)}
                    {(p.node.excerpt || "").length > 22 ? "…" : ""}
                  </text>
                </g>
              </g>
            );
          })}
        </svg>
      </div>

      {/* hover detail */}
      {hover && (
        <div className="panel px-3.5 py-2.5 animate-fade-in" style={{ background: "rgba(107,122,255,0.06)" }}>
          {(() => {
            const p = posById.get(hover)!;
            const st = eventStyle(p.node.event_type);
            const inEdges = graph.links.filter((l) => l.target === hover);
            const outEdges = graph.links.filter((l) => l.source === hover);
            return (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="badge text-2xs" style={{ background: st.bg, color: st.color }}>
                    {st.label}
                  </span>
                  <span className="text-xs text-text-primary">{p.node.excerpt || "（无摘录）"}</span>
                </div>
                <div className="text-2xs text-text-tertiary font-mono">
                  入边 {inEdges.length} · 出边 {outEdges.length}
                  {p.node.subjects?.length ? ` · 主体 ${p.node.subjects.length}` : ""}
                </div>
                {inEdges.length > 0 && (
                  <div className="text-2xs text-text-tertiary">
                    ← 因于：
                    {inEdges.map((l, i) => (
                      <span key={i} className="font-mono text-text-disabled">
                        {(posById.get(l.source)?.node.excerpt || "").slice(0, 10) || l.source.slice(0, 6)}
                        {l.mode === "hard" ? "!" : ""}
                        {i < inEdges.length - 1 ? "、" : ""}
                      </span>
                    ))}
                  </div>
                )}
                {outEdges.length > 0 && (
                  <div className="text-2xs text-text-tertiary">
                    → 导致：
                    {outEdges.map((l, i) => (
                      <span key={i} className="font-mono text-text-disabled">
                        {(posById.get(l.target)?.node.excerpt || "").slice(0, 10) || l.target.slice(0, 6)}
                        {l.mode === "hard" ? "!" : ""}
                        {i < outEdges.length - 1 ? "、" : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      )}

      {/* counterfactual audit */}
      {auditErr && (
        <div className="text-2xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">
          {auditErr}
        </div>
      )}
      {audit && (
        <div className="panel p-3.5 space-y-2 animate-slide-up">
          <div className="flex items-center gap-2">
            <ShieldQuestion size={12} className="text-brand-accent" />
            <span className="text-2xs text-text-primary" style={{ fontWeight: 510 }}>
              反事实审计 · 移除 {audit.audited_events?.length || 0} 个关键节点重放
            </span>
            {audit.findings.length === 0 && (
              <span className="badge bg-success-muted text-success text-2xs">无异常</span>
            )}
          </div>
          {audit.findings.length > 0 && (
            <div className="space-y-1.5 max-h-64 overflow-auto pr-1">
              {audit.findings.map((f, i) => {
                const b = CF_BADGE[f.classification] || {
                  label: f.classification,
                  cls: "text-text-secondary bg-bg-surface",
                };
                return (
                  <div
                    key={i}
                    className="flex items-start gap-2 text-2xs bg-bg-canvas/60 border border-border-subtle rounded-md px-2.5 py-2"
                    style={{ animation: `slideUp 0.25s ease-out ${i * 50}ms both` }}
                  >
                    <span className={clsx("badge text-2xs shrink-0", b.cls)}>{b.label}</span>
                    <div className="min-w-0">
                      <div className="text-text-secondary font-mono">
                        {f.removed_event_key} → {f.checked_target_key}
                      </div>
                      <div className="text-text-disabled mt-0.5">{f.detail}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
