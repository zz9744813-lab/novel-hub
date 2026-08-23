import { useEffect, useState, useRef } from "react";
import { api, GenreProfileSummary, ReferenceSample } from "../api";
import { Palette, RefreshCw, CheckCircle2, Clock, XCircle, Upload, Sparkles } from "lucide-react";
import { StyleMetricsPanel } from "./StyleMetricsPanel";

interface Props {
  bookId: string;
}

export function GenreProfilePanel({ bookId }: Props) {
  const [profiles, setProfiles] = useState<GenreProfileSummary[]>([]);
  const [samples, setSamples] = useState<ReferenceSample[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState("");
  const [editId, setEditId] = useState<string | null>(null);
  const [snippet, setSnippet] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    if (!bookId) {
      setProfiles([]);
      setSamples([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [p, s] = await Promise.all([
        api.genre.list(bookId),
        api.genre.listSamples(bookId),
      ]);
      setProfiles(p);
      setSamples(s);
    } catch (e: any) {
      setError(e.message);
      setProfiles([]);
      setSamples([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [bookId]);

  const onUpload = async (file: File) => {
    if (!bookId) return;
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const r = await api.genre.uploadSample(bookId, file, hint);
      setMsg(`已上传 ${r.filename} · ${r.character_count} 字 · ${r.sample_id}`);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const analyze = async (sampleId: string) => {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const r = await api.genre.analyze(bookId, sampleId);
      setMsg(`分析完成 → profile ${r.profile_id} v${r.version} · ${r.status}`);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const approve = async (id: string) => {
    setBusy(true);
    try {
      await api.genre.approve(id);
      setMsg(`已批准 ${id}`);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveEdit = async () => {
    if (!editId) return;
    setBusy(true);
    try {
      await api.genre.edit(editId, { prompt_injection_snippet: snippet });
      setMsg("已保存编辑");
      setEditId(null);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const statusIcon = (s: string) => {
    if (s === "approved" || s === "active") return <CheckCircle2 size={12} className="text-emerald-400" />;
    if (s === "rejected" || s === "superseded") return <XCircle size={12} className="text-red-400" />;
    return <Clock size={12} className="text-amber-400" />;
  };

  if (!bookId) {
    return (
      <div className="panel-elevated rounded-md px-4 py-6 space-y-2">
        <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
          Genre Profile
        </h2>
        <p className="text-xs text-text-tertiary">
          未选择作品也可进入本页。请在上方选择一本书后，再上传参考文本生成文风档案。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <StyleMetricsPanel bookId={bookId} />
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>Genre Profile</h2>
          <p className="text-2xs text-text-disabled font-mono mt-0.5">
            上传 → 分析 → Sanitizer → 编辑 → 批准
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
          <Upload size={13} className="text-brand-accent" />
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>上传参考文本</span>
        </div>
        <input
          className="w-full bg-bg-elevated border border-border rounded-md px-3 py-1.5 text-xs"
          placeholder="体裁提示（可选）"
          value={hint}
          onChange={(e) => setHint(e.target.value)}
        />
        <input
          ref={fileRef}
          type="file"
          accept=".txt,.md,.docx,.pdf,.rtf,.html"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
        />
        <button
          disabled={busy}
          onClick={() => fileRef.current?.click()}
          className="btn-primary px-3 py-1.5 text-xs rounded-md disabled:opacity-40"
        >
          选择文件上传
        </button>
      </div>

      <div className="panel-elevated rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-border text-xs text-text-secondary" style={{ fontWeight: 510 }}>
          Reference Samples · {samples.length}
        </div>
        {loading ? (
          <div className="p-4 text-xs text-text-disabled">加载中...</div>
        ) : samples.length === 0 ? (
          <div className="p-4 text-xs text-text-disabled">暂无参考样本</div>
        ) : (
          <div className="divide-y divide-border">
            {samples.map((s) => (
              <div key={s.id} className="px-3 py-2.5 flex items-center gap-3">
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-text-primary truncate">{s.filename}</div>
                  <div className="text-2xs font-mono text-text-disabled">
                    {s.status} · {s.character_count} 字
                  </div>
                </div>
                <button
                  disabled={busy || s.status === "analyzing"}
                  onClick={() => analyze(s.id)}
                  className="btn-ghost px-2 py-1 text-2xs rounded flex items-center gap-1"
                >
                  <Sparkles size={11} /> 分析
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel-elevated rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-border flex items-center gap-2">
          <Palette size={13} className="text-brand-accent" />
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>Profiles</span>
          <span className="ml-auto text-2xs font-mono text-text-disabled">{profiles.length}</span>
        </div>
        {profiles.length === 0 ? (
          <div className="p-6 text-center text-xs text-text-disabled">暂无 GenreProfile</div>
        ) : (
          <div className="divide-y divide-border">
            {profiles.map((p) => (
              <div key={p.id} className="px-3 py-3 space-y-2">
                <div className="flex items-center gap-3">
                  {statusIcon(p.status)}
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-text-primary" style={{ fontWeight: 510 }}>
                      v{p.version} · {p.status}
                      {p.narrative_person ? ` · ${p.narrative_person}` : ""}
                    </div>
                    <div className="text-2xs font-mono text-text-disabled mt-0.5 truncate">{p.id}</div>
                  </div>
                  {p.status !== "approved" && (
                    <>
                      <button
                        className="btn-ghost px-2 py-1 text-2xs rounded"
                        onClick={() => {
                          setEditId(p.id);
                          setSnippet(p.prompt_injection_snippet || "");
                        }}
                      >
                        编辑
                      </button>
                      <button
                        disabled={busy}
                        className="btn-primary px-2 py-1 text-2xs rounded"
                        onClick={() => approve(p.id)}
                      >
                        批准
                      </button>
                    </>
                  )}
                </div>
                {p.prompt_injection_snippet && (
                  <div className="text-2xs text-text-tertiary line-clamp-3">{p.prompt_injection_snippet}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {editId && (
        <div className="panel-elevated rounded-lg p-4 space-y-2">
          <div className="text-xs text-text-secondary">编辑 prompt_injection_snippet（200–500 字）</div>
          <textarea
            className="w-full h-32 bg-bg-elevated border border-border rounded-md px-3 py-2 text-xs"
            value={snippet}
            onChange={(e) => setSnippet(e.target.value)}
          />
          <div className="flex gap-2">
            <button className="btn-primary px-3 py-1.5 text-xs rounded" onClick={saveEdit} disabled={busy}>
              保存
            </button>
            <button className="btn-ghost px-3 py-1.5 text-xs rounded" onClick={() => setEditId(null)}>
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
