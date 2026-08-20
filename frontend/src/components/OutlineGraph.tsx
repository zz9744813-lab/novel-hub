import { useCallback, useEffect, useRef, useState } from "react";
import {
  Node,
  Edge,
  MarkerType,
  addEdge,
  Controls,
  Background,
  Panel,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

export interface OutlineNodeData extends Record<string, unknown> {
  title: string;
  status: "draft" | "writing" | "needs_human" | "completed" | "conflict";
  chapterNo?: number;
  conflictDetails?: string[];
}

interface OutlineGraphProps {
  bookId: string;
  nodes: (OutlineNodeData & { id: string })[];
  onResolveConflict?: (nodeId: string, conflictIndex: number) => void;
}

const STATUS_COLORS = {
  draft: "#7a808c",
  writing: "#8b8eff",
  needs_human: "#d4a24e",
  completed: "#27a644",
  conflict: "#e05555",
};

export function OutlineGraph({ bookId, nodes }: OutlineGraphProps) {
  const [nodesList, setNodesList] = useState<Node[]>([]);
  const [edgesList, setEdgesList] = useState<Edge[]>([]);
  const flowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Layout: chain layout for linear outline
    const sorted = [...nodes].sort((a, b) => {
      const aNum = a.chapterNo ?? Infinity;
      const bNum = b.chapterNo ?? Infinity;
      return aNum - bNum;
    });

    const newNodes: Node[] = sorted.map((n, i) => ({
      id: n.id,
      type: "default",
      position: { x: 0, y: i * 160 },
      data: { ...n } as Record<string, unknown>,
      style: {
        ...n.conflict ? { border: `2px solid ${STATUS_COLORS.conflict}` } : {},
        borderRadius: 14,
        padding: 14,
        background: "var(--app-surface)",
        color: (n as any).conflict ? STATUS_COLORS.conflict : "#f5f6f8",
        borderLeft: `4px solid ${STATUS_COLORS[n.status as keyof typeof STATUS_COLORS]}`,
      },
    }));

    const newEdges: Edge[] = [];
    for (let i = 0; i < newNodes.length - 1; i++) {
      newEdges.push({
        id: `${newNodes[i].id}-${newNodes[i + 1].id}`,
        source: newNodes[i].id,
        target: newNodes[i + 1].id,
        type: "animated",
        markerEnd: { type: MarkerType.ArrowClosed, color: STATUS_COLORS.writing },
      });
    }

    setNodesList(newNodes);
    setEdgesList(newEdges);
  }, [nodes]);

  const onConnect = useCallback(
    (params: any) => setEdgesList((eds) => addEdge({ ...params, type: "animated" }, eds)),
    []
  );

  return (
    <div className="outline-graph min-h-[600px] rounded-card border border-border bg-bg-panel">
      <ReactFlow
        nodes={nodesList}
        edges={edgesList}
        onConnect={onConnect}
        fitView
        attributionPosition="bottom-left"
      >
        <Controls />
        <Background gap={12} size={1} color="#50545c" />
        <Panel position="top-right" className="mt-4 mr-4">
          <div className="flex flex-col gap-2 text-caption">
            <span className="font-medium text-text-primary">图例</span>
            {(Object.keys(STATUS_COLORS) as Array<keyof typeof STATUS_COLORS>).map((s) => (
              <div key={s} className="flex items-center gap-2">
                <span
                  className="inline-block w-3 h-3 rounded-sm"
                  style={{ background: STATUS_COLORS[s] }}
                />
                <span className="text-text-secondary">
                  {s === "draft" && "草稿"}
                  {s === "writing" && "写作中"}
                  {s === "needs_human" && "待处理"}
                  {s === "completed" && "已完结"}
                  {s === "conflict" && "冲突"}
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </ReactFlow>
    </div>
  );
}
