import { useState } from "react";
import { api } from "../api";
import { Globe, CheckCircle2, Shield } from "lucide-react";

interface Props {
  bookId: string;
}

export function ResearchPanel({ bookId }: Props) {
  const [sessionId, setSessionId] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const approve = async () => {
    if (!sessionId.trim()) return;
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const r = await api.research.approve(sessionId.trim());
      setMsg(`会话 ${r.session_id} → ${r.status}`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>Research Sessions</h2>
        <p className="text-2xs text-text-disabled font-mono mt-0.5">C-32~C-34 · 外部调研 · 共享全局并发锁</p>
      </div>

      {error && <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">{error}</div>}
      {msg && <div className="text-xs text-emerald-400 bg-emerald-400/10 border border-emerald-400/20 rounded-md px-3 py-2">{msg}</div>}

      <div className="panel-elevated rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Globe size={13} className="text-brand-accent" />
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>审批调研结果</span>
        </div>
        <p className="text-2xs text-text-disabled">
          Book: <span className="font-mono">{bookId}</span>
        </p>
        <div className="flex gap-2">
          <input
            className="flex-1 bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-xs text-text-primary placeholder:text-text-disabled focus:outline-none focus:border-brand font-mono"
            placeholder="research_session_id (UUID)"
            value={sessionId}
            onChange={(e) => setSessionId(e.target.value)}
          />
          <button
            disabled={busy || !sessionId.trim()}
            onClick={approve}
            className="btn-primary px-3 py-1.5 text-xs rounded-md flex items-center gap-1.5 disabled:opacity-40"
          >
            <CheckCircle2 size={12} /> 批准入库
          </button>
        </div>
      </div>

      <div className="stat-card p-3 space-y-2">
        <div className="flex items-center gap-2 text-xs text-text-secondary" style={{ fontWeight: 510 }}>
          <Shield size={12} className="text-brand-accent" /> SafeContentFetcher
        </div>
        <ul className="text-2xs text-text-tertiary space-y-1 pl-1">
          <li>· SSRF 防护：拒绝私网/本地 IP</li>
          <li>· 内容大小上限 · 无 JS 执行</li>
          <li>· 与 Draft 共享 GLOBAL_LLM_CONCURRENCY</li>
          <li>· 调研结果需人工审批后才可进入 L3</li>
        </ul>
      </div>
    </div>
  );
}
