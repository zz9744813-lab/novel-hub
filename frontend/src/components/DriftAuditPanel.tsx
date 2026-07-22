import { useEffect, useState } from "react";
import { api } from "../api";
import { AlertTriangle, Loader2 } from "lucide-react";
import clsx from "clsx";

export function DriftAuditPanel({ bookId }: { bookId: string }) {
  const [audits, setAudits] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.audits.list(bookId).then((r) => {
      setAudits(Array.isArray(r) ? r : []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, [bookId]);

  const statusStyle = (s: string) =>
    s === "green" ? { color: "text-sage", bg: "bg-sage/10", label: "良好" } :
    s === "red"   ? { color: "text-red-400", bg: "bg-red-900/20", label: "红线" } :
    s === "yellow"? { color: "text-accent", bg: "bg-accent/10", label: "注意" } :
                   { color: "text-ink-400", bg: "bg-ink-800", label: s || "未知" };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <h2 className="text-2xl font-bold text-ink-200 font-serif flex items-center gap-2">
          <AlertTriangle size={24} className="text-accent" />
          漂移审计
        </h2>
        <p className="text-sm text-ink-500 mt-1">
          每 30 章自动触发 · 人物/世界观/叙事线一致性检测
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 size={24} className="animate-spin text-ink-600" />
        </div>
      ) : audits.length === 0 ? (
        <div className="flex flex-col items-center py-20">
          <div className="w-20 h-20 rounded-2xl bg-ink-850 flex items-center justify-center mb-6">
            <AlertTriangle size={36} className="text-ink-600" />
          </div>
          <h3 className="text-base text-ink-300 font-medium mb-2">尚未触发审计</h3>
          <p className="text-sm text-ink-500 max-w-xs text-center">
            DriftAudit 在累计 30 章后自动触发。当前章节数不足以构成审计周期
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {audits.map((a, i) => {
            const st = statusStyle(a.status);
            return (
              <div key={a.audit_id || i} className="p-5 bg-ink-900 rounded-xl border border-ink-800">
                <div className="flex items-center gap-3 mb-3">
                  <span className={clsx("badge", st.bg, st.color)}>
                    <span className="w-1.5 h-1.5 rounded-full bg-current" />
                    {st.label}
                  </span>
                  <span className="text-sm text-ink-400">
                    Ch.{a.chapter_range?.[0]} — Ch.{a.chapter_range?.[1]}
                  </span>
                  <span className="text-xs text-ink-600 ml-auto font-mono">
                    {new Date(a.created_at || a.timestamp || Date.now()).toLocaleString("zh-CN")}
                  </span>
                </div>
                {a.summary && <p className="text-sm text-ink-400">{a.summary}</p>}
                {a.issues && a.issues.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {a.issues.map((issue: any, j: number) => (
                      <li key={j} className="text-xs text-ink-500 flex items-start gap-1.5">
                        <span className="text-ink-600 mt-0.5">·</span>
                        {typeof issue === "string" ? issue : JSON.stringify(issue)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
