import { useEffect, useState } from "react";
import { api, ContextPackageSummary, ContextPackageDetail } from "../api";
import { Search, Package, ChevronRight, AlertCircle, CheckCircle2, XCircle } from "lucide-react";

interface Props {
  bookId: string;
}

export function ContextInspector({ bookId }: Props) {
  const [packages, setPackages] = useState<ContextPackageSummary[]>([]);
  const [selected, setSelected] = useState<ContextPackageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [chapterId, setChapterId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadByChapter = async (cid: string) => {
    if (!cid) return;
    setLoading(true);
    setError(null);
    try {
      const data = await api.chapters.contextPackages(cid);
      setPackages(data);
      setSelected(null);
    } catch (e: any) {
      setError(e.message);
      setPackages([]);
    } finally {
      setLoading(false);
    }
  };

  const openDetail = async (id: string) => {
    try {
      const d = await api.context.get(id);
      setSelected(d);
    } catch (e: any) {
      setError(e.message);
    }
  };

  useEffect(() => {
    setLoading(false);
  }, [bookId]);

  const stateIcon = (s: string) => {
    if (s === "published" || s === "ok" || s === "completed") return <CheckCircle2 size={12} className="text-emerald-400" />;
    if (s === "blocked" || s === "failed") return <XCircle size={12} className="text-red-400" />;
    return <AlertCircle size={12} className="text-amber-400" />;
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>Context Inspector</h2>
          <p className="text-2xs text-text-disabled font-mono mt-0.5">C-35 · agent_context_packages</p>
        </div>
      </div>

      <div className="flex gap-2">
        <input
          className="flex-1 bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-xs text-text-primary placeholder:text-text-disabled focus:outline-none focus:border-brand"
          placeholder="输入 chapter_id (UUID) 查询 Context Packages..."
          value={chapterId}
          onChange={(e) => setChapterId(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && loadByChapter(chapterId.trim())}
        />
        <button
          className="btn-primary px-3 py-1.5 text-xs rounded-md flex items-center gap-1.5"
          onClick={() => loadByChapter(chapterId.trim())}
        >
          <Search size={12} /> 查询
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">{error}</div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* List */}
        <div className="panel-elevated rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-border flex items-center gap-2">
            <Package size={13} className="text-brand-accent" />
            <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>Attempts</span>
            <span className="ml-auto text-2xs font-mono text-text-disabled">{packages.length}</span>
          </div>
          <div className="max-h-[420px] overflow-auto divide-y divide-border">
            {loading ? (
              <div className="p-4 text-xs text-text-disabled">加载中...</div>
            ) : packages.length === 0 ? (
              <div className="p-4 text-xs text-text-disabled">输入 chapter_id 后查询，或暂无记录</div>
            ) : (
              packages.map((p) => (
                <button
                  key={p.id}
                  onClick={() => openDetail(p.id)}
                  className="w-full text-left px-3 py-2.5 hover:bg-bg-hover transition-colors flex items-start gap-2"
                >
                  {stateIcon(p.publish_state)}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-text-primary" style={{ fontWeight: 510 }}>{p.agent_role}</span>
                      <span className="text-2xs font-mono text-text-disabled">#{p.attempt_no}</span>
                    </div>
                    <div className="text-2xs text-text-tertiary font-mono truncate mt-0.5">
                      {p.provider}/{p.model}
                    </div>
                    {p.block_reason && (
                      <div className="text-2xs text-red-400 mt-0.5 truncate">{p.block_reason}</div>
                    )}
                  </div>
                  <ChevronRight size={12} className="text-text-disabled mt-1" />
                </button>
              ))
            )}
          </div>
        </div>

        {/* Detail */}
        <div className="panel-elevated rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-border">
            <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>Package Detail</span>
          </div>
          {!selected ? (
            <div className="p-4 text-xs text-text-disabled">选择左侧条目查看详情</div>
          ) : (
            <div className="p-3 space-y-3 text-xs max-h-[420px] overflow-auto">
              <Row label="ID" value={selected.id} mono />
              <Row label="Run" value={selected.run_id} mono />
              <Row label="Agent" value={selected.agent_role} />
              <Row label="Model" value={`${selected.provider} / ${selected.model}`} mono />
              <Row label="Attempt" value={String(selected.attempt_no)} />
              <Row label="Publish" value={selected.publish_state} />
              {selected.block_reason && <Row label="Block" value={selected.block_reason} />}
              <Row label="Prompt Ver" value={selected.prompt_version} mono />
              <Row label="Template Hash" value={selected.prompt_template_hash?.slice(0, 16) + "…"} mono />
              <Row label="Rendered Hash" value={selected.rendered_prompt_hash?.slice(0, 16) + "…"} mono />
              <Row label="Token Est." value={String(selected.assembled_token_estimate ?? "—")} mono />
              <div>
                <div className="text-2xs text-text-disabled mb-1">Assembly Manifest</div>
                <pre className="bg-bg-canvas border border-border rounded p-2 text-2xs font-mono text-text-tertiary overflow-auto max-h-40">
                  {JSON.stringify(selected.assembly_manifest, null, 2)}
                </pre>
              </div>
              <div>
                <div className="text-2xs text-text-disabled mb-1">L4 Entity Refs</div>
                <pre className="bg-bg-canvas border border-border rounded p-2 text-2xs font-mono text-text-tertiary overflow-auto max-h-32">
                  {JSON.stringify(selected.l4_entity_refs, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-3">
      <span className="text-text-disabled w-28 shrink-0">{label}</span>
      <span className={`text-text-secondary break-all ${mono ? "font-mono text-2xs" : ""}`}>{value}</span>
    </div>
  );
}
