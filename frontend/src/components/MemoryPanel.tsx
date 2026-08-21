import { useEffect, useState } from "react";
import { api, L4Snapshot } from "../api";
import { CoreAnchorsEditor } from "./CoreAnchorsEditor";
import { Brain, Loader2, Lock, ChevronDown, ChevronRight, Anchor } from "lucide-react";
import clsx from "clsx";

export function MemoryPanel({ bookId }: { bookId: string }) {
  const [snapshots, setSnapshots] = useState<L4Snapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    api.memory.l4(bookId).then((r) => {
      setSnapshots(r.snapshots || []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [bookId]);

  const grouped = snapshots.reduce<Record<string, L4Snapshot[]>>((acc, s) => {
    (acc[s.entity_type] = acc[s.entity_type] || []).push(s);
    return acc;
  }, {});

  const toggleExpand = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-2 mb-4">
        <Brain size={14} className="text-text-disabled" />
        <h2 className="text-xs text-text-primary uppercase tracking-wider" style={{ fontWeight: 510 }}>记忆银行</h2>
        <span className="text-2xs text-text-disabled">L0-L4 权威状态库 · 定稿后自动生成持久化快照</span>
      </div>

      {/* v9 Core Anchors */}
      <div className="mb-5">
        <div className="flex items-center gap-2 mb-3">
          <Anchor size={14} className="text-ink" />
          <h3 className="text-xs text-text-primary uppercase tracking-wider" style={{ fontWeight: 510 }}>角色核心锚点</h3>
          <span className="text-2xs text-text-disabled">三层角色模型的稳定内核 · 约束动机归因（§7）</span>
        </div>
        <CoreAnchorsEditor bookId={bookId} />
      </div>

      <div className="flex items-center gap-2 mb-3">
        <Brain size={14} className="text-text-disabled" />
        <h3 className="text-xs text-text-secondary uppercase tracking-wider" style={{ fontWeight: 510 }}>L4 认知状态快照</h3>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 size={18} className="animate-spin text-text-disabled" />
        </div>
      ) : snapshots.length === 0 ? (
        <div className="panel flex flex-col items-center py-16 text-text-tertiary">
          <Brain size={28} className="mb-3 opacity-20" />
          <h3 className="text-xs text-text-secondary mb-1" style={{ fontWeight: 510 }}>L4 状态库为空</h3>
          <p className="text-2xs text-text-disabled max-w-xs text-center">
            生成章节并定稿后，系统会自动提取角色/事件/视角实体，持久化到 L4 权威状态库
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {Object.entries(grouped).map(([type, items]) => (
            <div key={type}>
              <div className="flex items-center gap-2 mb-2">
                <span className="badge bg-brand-muted text-brand-accent text-2xs">{type}</span>
                <span className="text-2xs text-text-disabled font-mono">{items.length} snapshots</span>
              </div>
              <div className="space-y-1">
                {items.map((s) => {
                  const isExpanded = expanded.has(s.id);
                  return (
                    <div key={s.id} className="panel overflow-hidden">
                      <button
                        onClick={() => toggleExpand(s.id)}
                        className="w-full flex items-center gap-2 p-3 text-left hover:bg-bg-hover transition-colors"
                      >
                        {isExpanded ? <ChevronDown size={12} className="text-text-disabled" /> : <ChevronRight size={12} className="text-text-disabled" />}
                        <span className="text-2xs font-mono text-text-tertiary">Ch.{s.as_of_chapter}</span>
                        <span className="text-2xs text-text-disabled font-mono">v{s.version}</span>
                        {s.is_locked && (
                          <span className="flex items-center gap-0.5 text-2xs text-brand-accent">
                            <Lock size={9} /> locked
                          </span>
                        )}
                        <span className="text-2xs text-text-tertiary ml-auto font-mono">
                          {isExpanded ? "收起" : "展开"}
                        </span>
                      </button>
                      {isExpanded && (
                        <div className="px-3 pb-3 animate-fade-in">
                          <pre className="text-2xs text-text-tertiary font-mono overflow-x-auto bg-bg-canvas p-2.5 rounded border border-border-subtle">
                            {JSON.stringify(s.state, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
