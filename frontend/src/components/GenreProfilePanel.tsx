import { useEffect, useState } from "react";
import { api, GenreProfileSummary } from "../api";
import { Palette, RefreshCw, CheckCircle2, Clock, XCircle } from "lucide-react";

interface Props {
  bookId: string;
}

export function GenreProfilePanel({ bookId }: Props) {
  const [profiles, setProfiles] = useState<GenreProfileSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.genre.list(bookId);
      setProfiles(data);
    } catch (e: any) {
      setError(e.message);
      setProfiles([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [bookId]);

  const statusIcon = (s: string) => {
    if (s === "approved" || s === "active") return <CheckCircle2 size={12} className="text-emerald-400" />;
    if (s === "rejected") return <XCircle size={12} className="text-red-400" />;
    return <Clock size={12} className="text-amber-400" />;
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>Genre Profile</h2>
          <p className="text-2xs text-text-disabled font-mono mt-0.5">C-27~C-29 · 参考文风档案 · 审批后注入写作</p>
        </div>
        <button onClick={load} className="btn-ghost px-2.5 py-1.5 text-xs rounded-md flex items-center gap-1.5">
          <RefreshCw size={12} /> 刷新
        </button>
      </div>

      {error && <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">{error}</div>}

      <div className="panel-elevated rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-border flex items-center gap-2">
          <Palette size={13} className="text-brand-accent" />
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>Profiles</span>
          <span className="ml-auto text-2xs font-mono text-text-disabled">{profiles.length}</span>
        </div>

        {loading ? (
          <div className="p-4 text-xs text-text-disabled">加载中...</div>
        ) : profiles.length === 0 ? (
          <div className="p-6 text-center space-y-2">
            <p className="text-xs text-text-disabled">暂无 GenreProfile</p>
            <p className="text-2xs text-text-disabled font-mono">
              POST /api/books/{"{id}"}/references 上传参考文本 → analyze → approve
            </p>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {profiles.map((p) => (
              <div key={p.id} className="px-3 py-3 flex items-center gap-3">
                {statusIcon(p.status)}
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-text-primary" style={{ fontWeight: 510 }}>
                    v{p.version} · {p.status}
                  </div>
                  <div className="text-2xs font-mono text-text-disabled mt-0.5 truncate">{p.id}</div>
                </div>
                <span className="text-2xs text-text-disabled">
                  {p.created_at ? new Date(p.created_at).toLocaleString() : "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="stat-card p-3 text-2xs text-text-tertiary space-y-1">
        <div>· 参考原文永不进入 DraftWriter（C-28）</div>
        <div>· StyleSanitizer 拦截 15 字连续照抄 / 5-gram Jaccard / 注入模式（C-29）</div>
        <div>· 仅 approved 状态的 Profile 可注入写作 Agent</div>
      </div>
    </div>
  );
}
