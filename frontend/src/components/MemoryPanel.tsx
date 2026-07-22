import { useEffect, useState } from "react";
import { api, L4Snapshot } from "../api";
import { Brain, Loader2, Lock } from "lucide-react";

export function MemoryPanel({ bookId }: { bookId: string }) {
  const [snapshots, setSnapshots] = useState<L4Snapshot[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.memory.l4(bookId).then((r) => {
      setSnapshots(r.snapshots || []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [bookId]);

  // Group by entity_type
  const grouped = snapshots.reduce<Record<string, L4Snapshot[]>>((acc, s) => {
    (acc[s.entity_type] = acc[s.entity_type] || []).push(s);
    return acc;
  }, {});

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-ink-200 font-serif flex items-center gap-2">
          <Brain size={24} className="text-accent" />
          记忆银行
        </h2>
        <p className="text-sm text-ink-500 mt-1">
          L0-L4 权威状态库 · 章节定稿后自动生成持久化快照
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 size={24} className="animate-spin text-ink-600" />
        </div>
      ) : snapshots.length === 0 ? (
        <div className="flex flex-col items-center py-20">
          <div className="w-20 h-20 rounded-2xl bg-ink-850 flex items-center justify-center mb-6">
            <Brain size={36} className="text-ink-600" />
          </div>
          <h3 className="text-base text-ink-300 font-medium mb-2">L4 状态库为空</h3>
          <p className="text-sm text-ink-500 max-w-xs text-center">
            生成章节并定稿后，系统会自动提取角色/事件/视角实体，持久化到 L4 权威状态库
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {Object.entries(grouped).map(([type, items]) => (
            <div key={type}>
              <div className="flex items-center gap-2 mb-3">
                <span className="badge bg-accent/10 text-accent">{type}</span>
                <span className="text-xs text-ink-500">{items.length} 条快照</span>
              </div>
              <div className="space-y-2">
                {items.map((s) => (
                  <div key={s.id} className="p-4 bg-ink-900 rounded-xl border border-ink-800">
                    <div className="flex items-center gap-3 mb-2">
                      <span className="text-xs font-mono text-ink-500">Ch.{s.as_of_chapter}</span>
                      <span className="text-xs text-ink-500">v{s.version}</span>
                      {s.is_locked && (
                        <span className="flex items-center gap-1 text-xs text-accent">
                          <Lock size={10} /> locked
                        </span>
                      )}
                    </div>
                    <pre className="text-xs text-ink-400 font-mono overflow-x-auto bg-ink-950 p-3 rounded-lg">
                      {JSON.stringify(s.state, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
