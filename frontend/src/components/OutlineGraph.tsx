import { useCallback, useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import clsx from "clsx";
import { ListChecks, Maximize2, Target } from "lucide-react";
import type { OutlineNode } from "../api";

// ── 状态模型 ──────────────────────────────────────────────
export type OutlineNodeStatus =
  | "draft"
  | "writing"
  | "needs_human"
  | "completed"
  | "conflict";

/** 大纲节点 + 推断出的写作状态 */
export interface OutlineViewNode extends OutlineNode {
  status: OutlineNodeStatus;
  word_count?: number;
}

interface OutlineGraphProps {
  nodes: OutlineViewNode[];
  onNodeClick?: (node: OutlineViewNode) => void;
  layoutKey?: string;
}

// ── 状态视觉语言（与 ChapterList 的语义色对齐）────────────
const STATUS_META: Record<
  OutlineNodeStatus,
  { label: string; dot: string; text: string; ring: string }
> = {
  draft: { label: "未开写", dot: "#7a808c", text: "text-text-tertiary", ring: "rgba(122,128,140,0.5)" },
  writing: { label: "写作中", dot: "#8b8eff", text: "text-brand-accent", ring: "rgba(139,142,255,0.6)" },
  needs_human: { label: "待人工", dot: "#d4a24e", text: "text-warning", ring: "rgba(212,162,78,0.6)" },
  completed: { label: "已定稿", dot: "#27a644", text: "text-success", ring: "rgba(39,166,68,0.6)" },
  conflict: { label: "失败", dot: "#e05555", text: "text-danger", ring: "rgba(224,85,85,0.65)" },
};

// ── 自定义节点卡片 ────────────────────────────────────────
interface OutlineCardData extends Record<string, unknown> {
  chapter_no: number;
  title: string;
  goal: string;
  status: OutlineNodeStatus;
  beats: number;
  deps: number[];
}

function OutlineCardNode(props: NodeProps) {
  const data = props.data as unknown as OutlineCardData;
  const meta = STATUS_META[data.status] ?? STATUS_META.draft;
  const selected = props.selected;

  return (
    <div
      className={clsx(
        "group relative w-[240px] rounded-card border bg-bg-surface px-3.5 py-3 text-left transition-all duration-200",
        selected
          ? "border-brand-accent shadow-glow"
          : "border-border hover:border-border-strong hover:shadow-card-hover hover:-translate-y-0.5"
      )}
      style={{ boxShadow: selected ? undefined : "0 1px 2px rgba(0,0,0,0.3)" }}
    >
      {/* 左侧状态条 */}
      <span
        className="absolute left-0 top-3 bottom-3 w-[3px] rounded-full"
        style={{ background: meta.dot }}
      />
      {/* 章节号 + 状态徽标 */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className="font-mono text-2xs text-text-tertiary">Ch.{data.chapter_no}</span>
        <span className={clsx("ml-auto inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-2xs font-medium", meta.text)}>
          <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: meta.dot }} />
          {meta.label}
        </span>
      </div>
      {/* 标题 */}
      <div className="text-body font-medium text-text-primary leading-snug line-clamp-1">
        {data.title || "（无标题）"}
      </div>
      {/* goal 摘要 */}
      {data.goal && (
        <div className="mt-1 text-2xs text-text-tertiary leading-snug line-clamp-2">
          {data.goal}
        </div>
      )}
      {/* 底部指标 */}
      <div className="mt-2.5 flex items-center gap-3 text-2xs text-text-disabled">
        <span className="inline-flex items-center gap-1">
          <ListChecks size={11} />
          {data.beats} 拍
        </span>
        {data.deps.length > 0 && (
          <span className="inline-flex items-center gap-1" title={`依赖章节 ${data.deps.join("、")}`}>
            <Target size={11} />
            依赖 {data.deps.length}
          </span>
        )}
      </div>

      <Handle type="target" position={Position.Top} className="!bg-border-strong !w-2 !h-2" />
      <Handle type="source" position={Position.Bottom} className="!bg-border-strong !w-2 !h-2" />
    </div>
  );
}

const nodeTypes = { outlineCard: OutlineCardNode };

// ── 依赖解析：depends_on 可能是 int[]（chapter_no）或 [{node_id}] ──
function resolveDeps(node: OutlineNode, all: OutlineNode[]): number[] {
  const raw = (node.depends_on ?? []) as any[];
  const byId = new Map(all.map((n) => [n.node_id, n.chapter_no]));
  const out: number[] = [];
  for (const dep of raw) {
    if (typeof dep === "number") {
      out.push(dep);
      continue;
    }
    if (typeof dep === "string") {
      const num = Number(dep);
      if (!Number.isNaN(num)) {
        out.push(num);
        continue;
      }
      const mapped = byId.get(dep);
      if (mapped != null) out.push(mapped);
      continue;
    }
    if (dep && typeof dep === "object") {
      const id = (dep as any).node_id ?? (dep as any).id;
      if (typeof id === "number") {
        out.push(id);
        continue;
      }
      const mapped = byId.get(String(id));
      if (mapped != null) out.push(mapped);
    }
  }
  return [...new Set(out)];
}

// ── 布局持久化（拖拽重排后位置保存到 localStorage）────────
const LAYOUT_PREFIX = "novelforge.outline.layout.";

function readLayout(key: string): Record<string, { x: number; y: number }> {
  try {
    const raw = localStorage.getItem(LAYOUT_PREFIX + key);
    return raw ? (JSON.parse(raw) as Record<string, { x: number; y: number }>) : {};
  } catch {
    return {};
  }
}

function writeLayout(key: string, nodes: Node[]) {
  try {
    const pos: Record<string, { x: number; y: number }> = {};
    for (const n of nodes) pos[n.id] = { x: n.position.x, y: n.position.y };
    localStorage.setItem(LAYOUT_PREFIX + key, JSON.stringify(pos));
  } catch {
    /* ignore */
  }
}

function clearLayout(key: string) {
  try {
    localStorage.removeItem(LAYOUT_PREFIX + key);
  } catch {
    /* ignore */
  }
}

// ── 主组件 ────────────────────────────────────────────────
export function OutlineGraph({ nodes, onNodeClick, layoutKey }: OutlineGraphProps) {
  const [rfNodes, setRfNodes] = useState<Node[]>([]);
  const [rfEdges, setRfEdges] = useState<Edge[]>([]);
  const [layoutVersion, setLayoutVersion] = useState(0);

  // 构建 DAG：按 chapter_no 线性布局，depends_on 额外画依赖边
  useEffect(() => {
    const sorted = [...nodes].sort((a, b) => a.chapter_no - b.chapter_no);
    const byChapterNo = new Map(sorted.map((n) => [n.chapter_no, n]));
    const saved = layoutKey ? readLayout(layoutKey) : {};

    const ROW_H = 150;

    const newNodes: Node[] = sorted.map((n, i) => {
      const deps = resolveDeps(n, nodes);
      const id = n.node_id || String(n.chapter_no);
      const savedPos = saved[id];
      return {
        id,
        type: "outlineCard",
        position: savedPos ?? { x: 0, y: i * ROW_H },
        data: {
          chapter_no: n.chapter_no,
          title: n.title,
          goal: n.goal,
          status: n.status,
          beats: (n.required_beats ?? []).length,
          deps,
        } as OutlineCardData,
      };
    });

    // 主边：线性顺序（细、灰）
    // 依赖边：depends_on 指向（粗、品牌色、动画）
    const edges: Edge[] = [];
    for (let i = 0; i < newNodes.length - 1; i++) {
      edges.push({
        id: `seq-${newNodes[i].id}-${newNodes[i + 1].id}`,
        source: newNodes[i].id,
        target: newNodes[i + 1].id,
        style: { stroke: "rgba(255,255,255,0.14)", strokeWidth: 1.5 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "rgba(255,255,255,0.2)", width: 14, height: 14 },
      });
    }

    // 依赖边（跳过顺序相邻，避免重复）
    const seen = new Set(edges.map((e) => `${e.source}->${e.target}`));
    for (const n of sorted) {
      const deps = resolveDeps(n, nodes);
      for (const depChapterNo of deps) {
        const src = byChapterNo.get(depChapterNo);
        if (!src) continue;
        const srcId = src.node_id || String(src.chapter_no);
        const tgtId = n.node_id || String(n.chapter_no);
        const key = `${srcId}->${tgtId}`;
        if (seen.has(key)) continue;
        // 只画"回看"依赖（dep < 当前章），其余画顺序边已覆盖
        if (depChapterNo === n.chapter_no - 1) continue;
        seen.add(key);
        edges.push({
          id: `dep-${srcId}-${tgtId}`,
          source: srcId,
          target: tgtId,
          type: "smoothstep",
          animated: true,
          style: { stroke: "rgba(139,142,255,0.55)", strokeWidth: 2 },
          markerEnd: { type: MarkerType.ArrowClosed, color: "#8b8eff", width: 16, height: 16 },
        });
      }
    }

    setRfNodes(newNodes);
    setRfEdges(edges);
  }, [nodes, layoutKey, layoutVersion]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setRfNodes((nds) => applyNodeChanges(changes, nds)),
    []
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => setRfEdges((eds) => applyEdgeChanges(changes, eds)),
    []
  );
  const onConnect = useCallback(
    (params: Connection) =>
      setRfEdges((eds) =>
        addEdge(
          {
            ...params,
            type: "smoothstep",
            animated: true,
            style: { stroke: "rgba(139,142,255,0.55)", strokeWidth: 2 },
            markerEnd: { type: MarkerType.ArrowClosed, color: "#8b8eff" },
          },
          eds
        )
      ),
    []
  );

  const onNodeDragStop = useCallback(() => {
    if (!layoutKey) return;
    setRfNodes((nds) => {
      writeLayout(layoutKey, nds);
      return nds;
    });
  }, [layoutKey]);

  const resetLayout = useCallback(() => {
    if (layoutKey) clearLayout(layoutKey);
    setLayoutVersion((v) => v + 1);
  }, [layoutKey]);

  const onNodeClickHandler = useCallback(
    (_: ReactMouseEvent, node: Node) => {
      const data = node.data as unknown as OutlineCardData;
      const viewNode = nodes.find(
        (n) => (n.node_id || String(n.chapter_no)) === node.id
      );
      if (viewNode) onNodeClick?.(viewNode);
      else if (data) {
        onNodeClick?.({
          node_id: node.id,
          chapter_no: data.chapter_no,
          title: data.title,
          goal: data.goal,
          depends_on: [],
          required_beats: [],
          status: data.status,
        });
      }
    },
    [nodes, onNodeClick]
  );

  const legend = useMemo(
    () =>
      (Object.keys(STATUS_META) as OutlineNodeStatus[]).map((s) => ({
        key: s,
        ...STATUS_META[s],
      })),
    []
  );

  return (
    <div className="outline-graph relative h-full min-h-[560px] w-full rounded-card border border-border bg-bg-panel overflow-hidden">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClickHandler}
        onNodeDragStop={onNodeDragStop}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        minZoom={0.2}
        maxZoom={1.5}
        defaultEdgeOptions={{ type: "smoothstep" }}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="rgba(122,128,140,0.28)" />
        <Controls position="bottom-left" showInteractive={false} />
        <MiniMap
          position="bottom-right"
          pannable
          zoomable
          nodeColor={(n) => {
            const s = (n.data as OutlineCardData)?.status as OutlineNodeStatus;
            return STATUS_META[s]?.dot ?? "#7a808c";
          }}
          maskColor="rgba(0,0,0,0.6)"
        />
      </ReactFlow>

      {/* 图例 + 布局操作 */}
      <div className="pointer-events-none absolute right-3 top-3 flex flex-col items-end gap-2">
        <div className="rounded-md border border-border bg-bg-panel/80 px-3 py-2 backdrop-blur">
          <div className="mb-1.5 text-2xs font-medium text-text-primary">图例</div>
          <div className="flex flex-col gap-1">
            {legend.map((s) => (
              <div key={s.key} className="flex items-center gap-2 text-2xs text-text-secondary">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: s.dot }} />
                {s.label}
              </div>
            ))}
          </div>
        </div>
        {layoutKey && (
          <button
            onClick={resetLayout}
            className="pointer-events-auto flex items-center gap-1 rounded-md border border-border bg-bg-panel/80 px-2.5 py-1.5 text-2xs text-text-secondary backdrop-blur transition-colors hover:text-text-primary"
            title="恢复默认布局"
          >
            <Maximize2 size={12} />
            重置布局
          </button>
        )}
      </div>
    </div>
  );
}
