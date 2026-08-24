import { useEffect, useState } from "react";
import { api, WritingSessionView } from "../../api";
import { Loader2 } from "lucide-react";

const STOP_LABELS: Record<string, string> = {
  needs_human: "需要人工",
  causal_hard_failure: "因果硬失败",
  resource_blocked: "资源阻断",
  quality_drop: "质量下降",
  consecutive_bad_reviews: "连续退回",
  deadline: "时间到",
  outline_exhausted: "大纲写满",
  outline_node_missing: "章纲缺失",
  chapter_run_failed: "运行失败",
  cancelled: "人工结束",
};

export function WritingSessionHistory({ bookId }: { bookId: string }) {
  const [items, setItems] = useState<WritingSessionView[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.writingSessions.history(bookId).then((r) => {
      if (!cancelled) setItems(r.items);
    }).catch((e: any) => {
      if (!cancelled) setErr(e?.message || String(e));
    });
    return () => { cancelled = true; };
  }, [bookId]);

  if (err) return null;
  if (!items) {
    return (
      <div className="panel p-3 flex items-center gap-2 text-2xs text-text-tertiary">
        <Loader2 size={12} className="animate-spin" /> 加载写作会话历史…
      </div>
    );
  }
  if (!items.length) return null;

  return (
    <div className="panel p-4">
      <div className="text-2xs text-text-disabled mb-2">自动写作历史</div>
      <div className="flex flex-wrap gap-2">
        {items.slice(0, 20).map((s) => (
          <div key={s.id} className="rounded-md border border-border bg-bg-base px-2 py-1.5 text-2xs space-y-0.5 min-w-36">
            <div className="text-text-primary">
              {(s.started_at ? new Date(s.started_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—")}
            </div>
            <div className="text-text-tertiary">
              {s.chapters_completed} 章 · {(s.words_generated || 0).toLocaleString()} 字
            </div>
            <div className="text-text-disabled">
              {s.status === "completed" || s.status === "cancelled"
                ? STOP_LABELS[s.stop_reason || s.status] || s.stop_reason || s.status
                : s.status}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
