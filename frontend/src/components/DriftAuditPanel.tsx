import { useEffect, useState } from "react";
import { api } from "../api";
import { AlertTriangle, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import clsx from "clsx";

export function DriftAuditPanel({ bookId }: { bookId: string }) {
  const [audits, setAudits] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    api.audits.list(bookId).then((r) => {
      setAudits(Array.isArray(r) ? r : []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [bookId]);

  const statusStyle = (s: string) =>
    s === "green"  ? { color: "text-success", bg: "bg-success-muted", label: "良好" } :
    s === "red"    ? { color: "text-danger",  bg: "bg-danger-muted",  label: "红线" } :
    s === "yellow" ? { color: "text-warning", bg: "bg-warning-muted", label: "注意" } :
                     { color: "text-text-tertiary", bg: "bg-bg-surface", label: s || "未知" };

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
        <AlertTriangle size={14} className="text-text-disabled" />
        <h2 className="text-xs text-text-primary uppercase tracking-wider" style={{ fontWeight: 510 }}>漂移审计</h2>
        <span className="text-2xs text-text-disabled">每 30 章自动触发 · 一致性检测</span>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 size={18} className="animate-spin text-text-disabled" />
        </div>
      ) : audits.length === 0 ? (
        <div className="panel flex flex-col items-center py-16 text-text-tertiary">
          <AlertTriangle size={28} className="mb-3 opacity-20" />
          <h3 className="text-xs text-text-secondary mb-1" style={{ fontWeight: 510 }}>尚未触发审计</h3>
          <p className="text-2xs text-text-disabled max-w-xs text-center">
            DriftAudit 在累计 30 章后自动触发。当前章节数不足以构成审计周期
          </p>
        </div>
      ) : (
        <div className="space-y-1">
          {audits.map((a, i) => {
            const st = statusStyle(a.status);
            const id = a.audit_id || String(i);
            const isExpanded = expanded.has(id);
            return (
              <div key={id} className="panel overflow-hidden">
                <button
                  onClick={() => toggleExpand(id)}
                  className="w-full flex items-center gap-2 p-3 text-left hover:bg-bg-hover transition-colors"
                >
                  {isExpanded ? <ChevronDown size={12} className="text-text-disabled" /> : <ChevronRight size={12} className="text-text-disabled" />}
                  <span className={clsx("badge", st.bg, st.color, "text-2xs")}>
                    <span className="w-1 h-1 rounded-full bg-current" />
                    {st.label}
                  </span>
                  <span className="text-2xs text-text-tertiary font-mono">
                    Ch.{a.chapter_range?.[0]} — Ch.{a.chapter_range?.[1]}
                  </span>
                  <span className="text-2xs text-text-disabled font-mono ml-auto">
                    {new Date(a.created_at || a.timestamp || Date.now()).toLocaleString("zh-CN")}
                  </span>
                </button>
                {isExpanded && (
                  <div className="px-3 pb-3 animate-fade-in">
                    {a.summary && <p className="text-xs text-text-secondary mb-2">{a.summary}</p>}
                    {a.issues && a.issues.length > 0 && (
                      <ul className="space-y-1">
                        {a.issues.map((issue: any, j: number) => (
                          <li key={j} className="text-2xs text-text-tertiary flex items-start gap-1.5">
                            <span className="text-text-disabled mt-0.5">·</span>
                            {typeof issue === "string" ? issue : JSON.stringify(issue)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
