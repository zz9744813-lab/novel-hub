import { useEffect, useState } from "react";
import { api, ResearchSessionSummary, ResearchSessionDetail } from "../api";
import { Globe, CheckCircle2, Shield, Plus, RefreshCw } from "lucide-react";

interface Props {
  bookId: string;
}

export function ResearchPanel({ bookId }: Props) {
  const [topic, setTopic] = useState("");
  const [urls, setUrls] = useState("");
  const [sessions, setSessions] = useState<ResearchSessionSummary[]>([]);
  const [detail, setDetail] = useState<ResearchSessionDetail | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    if (!bookId) {
      setSessions([]);
      return;
    }
    try {
      setSessions(await api.research.list(bookId));
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    load();
  }, [bookId]);

  const create = async () => {
    if (!bookId || !topic.trim()) return;
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const urlList = urls
        .split("\n")
        .map((u) => u.trim())
        .filter(Boolean);
      const r = await api.research.create(bookId, {
        topic: topic.trim(),
        urls: urlList,
        search: true,
        max_results: 5,
      });
      setMsg(`会话 ${r.session_id} · ${r.status} · 证据 ${r.evidence_count}`);
      setTopic("");
      await load();
      const d = await api.research.get(r.session_id);
      setDetail(d);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const approve = async (sessionId: string) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.research.approve(sessionId);
      setMsg(`会话 ${r.session_id} → ${r.status}`);
      await load();
      if (detail?.id === sessionId) {
        setDetail(await api.research.get(sessionId));
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!bookId) {
    return (
      <div className="panel-elevated rounded-md px-4 py-6 space-y-2">
        <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
          Research Sessions
        </h2>
        <p className="text-xs text-text-tertiary">
          未选择作品也可进入本页。请在上方选择一本书后再创建调研会话。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>Research Sessions</h2>
          <p className="text-2xs text-text-disabled font-mono mt-0.5">
            plan → search → fetch → synthesize → approve
          </p>
        </div>
        <button onClick={load} className="btn-ghost px-2.5 py-1.5 text-xs rounded-md flex items-center gap-1.5">
          <RefreshCw size={12} /> 刷新
        </button>
      </div>

      {error && <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">{error}</div>}
      {msg && <div className="text-xs text-emerald-400 bg-emerald-400/10 border border-emerald-400/20 rounded-md px-3 py-2">{msg}</div>}

      <div className="panel-elevated rounded-lg p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Plus size={13} className="text-brand-accent" />
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>新建调研</span>
        </div>
        <input
          className="w-full bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-xs font-mono"
          placeholder="调研主题 / topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
        />
        <textarea
          className="w-full h-20 bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-xs font-mono"
          placeholder="可选：手动 URL，每行一个（跳过搜索或补充）"
          value={urls}
          onChange={(e) => setUrls(e.target.value)}
        />
        <button
          disabled={busy || !topic.trim()}
          onClick={create}
          className="btn-primary px-3 py-1.5 text-xs rounded-md flex items-center gap-1.5 disabled:opacity-40"
        >
          <Globe size={12} /> 开始调研
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="panel-elevated rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-border text-xs text-text-secondary" style={{ fontWeight: 510 }}>
            Sessions · {sessions.length}
          </div>
          <div className="divide-y divide-border max-h-[360px] overflow-auto">
            {sessions.length === 0 ? (
              <div className="p-4 text-xs text-text-disabled">暂无会话</div>
            ) : (
              sessions.map((s) => (
                <div key={s.id} className="px-3 py-2.5 flex items-center gap-2">
                  <button
                    className="flex-1 text-left min-w-0"
                    onClick={async () => setDetail(await api.research.get(s.id))}
                  >
                    <div className="text-xs text-text-primary truncate">{s.requested_topic}</div>
                    <div className="text-2xs font-mono text-text-disabled">{s.status} · {s.id.slice(0, 8)}…</div>
                  </button>
                  {s.status !== "approved" && (
                    <button
                      disabled={busy}
                      className="btn-ghost px-2 py-1 text-2xs rounded flex items-center gap-1"
                      onClick={() => approve(s.id)}
                    >
                      <CheckCircle2 size={11} /> 批准
                    </button>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        <div className="panel-elevated rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-border text-xs text-text-secondary" style={{ fontWeight: 510 }}>
            Evidence
          </div>
          {!detail ? (
            <div className="p-4 text-xs text-text-disabled">选择会话查看证据</div>
          ) : (
            <div className="p-3 space-y-2 max-h-[360px] overflow-auto">
              <div className="text-2xs text-text-disabled font-mono">{detail.status} · {detail.id}</div>
              {(detail.evidence || []).length === 0 ? (
                <div className="text-xs text-text-disabled">无证据（搜索可能为空，可填手动 URL）</div>
              ) : (
                detail.evidence.map((e) => (
                  <div key={e.id} className="border border-border rounded-md p-2 space-y-1">
                    <div className="text-xs text-text-primary truncate">{e.source_title || e.source_url}</div>
                    <a className="text-2xs text-brand-accent font-mono break-all" href={e.source_url} target="_blank" rel="noreferrer">
                      {e.source_url}
                    </a>
                    <div className="text-2xs text-text-tertiary">{e.summary?.slice(0, 280)}</div>
                  </div>
                ))
              )}
            </div>
          )}
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
          <li>· 调研结果需人工审批后才可进入写作上下文</li>
        </ul>
      </div>
    </div>
  );
}
