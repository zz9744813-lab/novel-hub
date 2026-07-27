import { useEffect, useState } from "react";
import { api, ContextPackageSummary, ContextPackageDetail, ChapterListItem } from "../api";
import { Search, Package, ChevronRight, AlertCircle, CheckCircle2, XCircle, FileText } from "lucide-react";
import { agentRoleLabel } from "../agentLabels";

interface Props {
  bookId: string;
}

export function ContextInspector({ bookId }: Props) {
  const [packages, setPackages] = useState<ContextPackageSummary[]>([]);
  const [selected, setSelected] = useState<ContextPackageDetail | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [chapters, setChapters] = useState<ChapterListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [chapterId, setChapterId] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!bookId) {
      setChapters([]);
      return;
    }
    api.chapters
      .list(bookId)
      .then(setChapters)
      .catch(() => setChapters([]));
  }, [bookId]);

  const loadByChapter = async (cid: string) => {
    if (!cid) return;
    setLoading(true);
    setError(null);
    setChapterId(cid);
    try {
      const data = await api.chapters.contextPackages(cid);
      setPackages(data);
      setSelected(null);
      setPreview(null);
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
      setPreview(null);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const openPreview = async (id: string) => {
    try {
      const p = await api.context.promptPreview(id);
      setPreview(p);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const stateIcon = (s: string) => {
    if (s === "published" || s === "ok" || s === "completed" || s === "publishable")
      return <CheckCircle2 size={12} className="text-emerald-400" />;
    if (s === "blocked" || s === "failed") return <XCircle size={12} className="text-red-400" />;
    return <AlertCircle size={12} className="text-amber-400" />;
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>Context Inspector</h2>
          <p className="text-2xs text-text-disabled font-mono mt-0.5">C-35 · agent_context_packages · prompt preview</p>
        </div>
      </div>

      {bookId && chapters.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chapters.map((c) => (
            <button
              key={c.chapter_id}
              onClick={() => loadByChapter(c.chapter_id)}
              className={`px-2 py-1 rounded text-2xs font-mono border ${
                chapterId === c.chapter_id
                  ? "border-brand bg-brand/10 text-brand-accent"
                  : "border-border text-text-tertiary hover:border-brand/40"
              }`}
            >
              Ch{c.chapter_no} · {c.status}
            </button>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input
          className="flex-1 bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-xs text-text-primary placeholder:text-text-disabled focus:outline-none focus:border-brand font-mono"
          placeholder="或输入 chapter_id (UUID)..."
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
              <div className="p-4 text-xs text-text-disabled">选择章节或输入 chapter_id 查询</div>
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
                      <span className="text-text-primary" style={{ fontWeight: 510 }} title={p.agent_role}>{agentRoleLabel(p.agent_role)}</span>
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

        <div className="panel-elevated rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-border flex items-center justify-between">
            <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>包详情</span>
            {selected && (
              <button
                className="btn-ghost px-2 py-1 text-2xs rounded flex items-center gap-1"
                onClick={() => openPreview(selected.id)}
              >
                <FileText size={11} /> Prompt 重建
              </button>
            )}
          </div>
          {!selected ? (
            <div className="p-4 text-xs text-text-disabled">选择左侧条目查看详情</div>
          ) : (
            <div className="p-3 space-y-3 text-xs max-h-[420px] overflow-auto">
              <Row label="ID" value={selected.id} mono />
              <Row label="Run" value={selected.run_id} mono />
              <Row label="角色" value={agentRoleLabel(selected.agent_role)} />
              <Row label="模型" value={`${selected.provider} / ${selected.model}`} mono />
              <Row label="尝试" value={String(selected.attempt_no)} />
              <Row label="发布状态" value={selected.publish_state} />
              {selected.block_reason && <Row label="阻断原因" value={selected.block_reason} />}
              <Row label="Prompt 版本" value={selected.prompt_version} mono />
              <Row label="模板哈希" value={(selected.prompt_template_hash || "").slice(0, 16) + "…"} mono />
              <Row label="渲染哈希" value={(selected.rendered_prompt_hash || "").slice(0, 16) + "…"} mono />
              <Row label="Token 估算" value={String(selected.assembled_token_estimate ?? "—")} mono />
              <div>
                <div className="text-2xs text-text-disabled mb-1">装配清单 Manifest</div>
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
              {preview && (
                <div>
                  <div className="text-2xs text-text-disabled mb-1">Prompt Preview (skeleton)</div>
                  <pre className="bg-bg-canvas border border-border rounded p-2 text-2xs font-mono text-text-tertiary overflow-auto max-h-48">
                    {JSON.stringify(preview, null, 2)}
                  </pre>
                </div>
              )}
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
