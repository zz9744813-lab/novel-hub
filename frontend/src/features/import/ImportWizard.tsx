import { useEffect, useState } from "react";
import { api } from "../../api";
import { Loader2, Upload, FileText, Sparkles } from "lucide-react";

export function ImportWizard({
  onClose,
  onCommitted,
}: {
  onClose: () => void;
  onCommitted: (bookId: string) => void;
}) {
  const [mode, setMode] = useState<"choose" | "upload" | "blank" | "preview" | "analyzing">("choose");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [session, setSession] = useState<any>(null);
  const [title, setTitle] = useState("");
  const [blankTitle, setBlankTitle] = useState("");
  const [conflictSelections, setConflictSelections] = useState<Record<string, string>>({});

  // poll while analyzing
  useEffect(() => {
    if (!sessionId || mode !== "analyzing") return;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await api.imports.get(sessionId);
        if (cancelled) return;
        setSession(s);
        if (s.status === "preview_ready" || s.status === "needs_human") {
          const p = await api.imports.preview(sessionId);
          if (cancelled) return;
          setPreview(p);
          setTitle(p.preview?.title_guess || p.preview?.metadata?.title || "");
          setMode("preview");
        } else if (s.status === "failed") {
          setError(s.error_detail || s.error_code || "分析失败");
        }
      } catch (e: any) {
        if (!cancelled) setError(e?.message || String(e));
      }
    };
    tick();
    const id = setInterval(tick, 2500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [sessionId, mode]);

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.imports.create(file);
      setSessionId(r.import_session_id);
      if (r.status === "preview_ready" || r.status === "needs_human") {
        const p = await api.imports.preview(r.import_session_id);
        setPreview(p);
        setTitle(p.preview?.title_guess || file.name.replace(/\.[^.]+$/, ""));
        setMode("preview");
      } else {
        setMode("analyzing");
      }
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const resolveWarnings = async () => {
    if (!sessionId) return;
    setBusy(true);
    setError(null);
    try {
      await api.imports.resolveBatch(sessionId, "warnings");
      const p = await api.imports.preview(sessionId);
      setPreview(p);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (!sessionId || !preview) return;
    setBusy(true);
    setError(null);
    try {
      // server auto_resolve_warnings=true by default; one click commit
      const r = await api.imports.commit(sessionId, {
        expected_preview_hash: preview.preview_hash,
        book_overrides: { title: title || preview.preview?.title_guess },
        auto_resolve_warnings: true,
      } as any);
      onCommitted(r.book_id);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const createBlank = async () => {
    if (!blankTitle.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.books.create({ title: blankTitle.trim(), target_chapters: 500 });
      onCommitted(r.book_id);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const counts = preview?.preview?.counts || {};
  const characters = preview?.preview?.characters || [];
  const chapters = preview?.preview?.chapters || [];
  const locations = preview?.preview?.world?.locations || [];
  const openWarnings = (preview?.conflicts || []).filter(
    (c: any) => c.status === "open" && c.severity !== "blocking"
  );
  const openBlocking = (preview?.conflicts || []).filter(
    (c: any) => c.status === "open" && c.severity === "blocking"
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-canvas/60 p-4" onClick={onClose}>
      <div
        className="bg-bg-elevated border border-border rounded-lg w-full max-w-xl max-h-[90vh] overflow-auto p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
            新建小说
          </h2>
          <button className="text-2xs text-text-disabled" onClick={onClose}>
            关闭
          </button>
        </div>

        {mode === "choose" && (
          <div className="grid grid-cols-1 gap-3">
            <button className="panel p-4 text-left hover:border-brand/40 transition-colors" onClick={() => setMode("upload")}>
              <div className="flex items-center gap-2 text-sm text-text-primary" style={{ fontWeight: 510 }}>
                <Sparkles size={15} className="text-brand-accent" />
                从企划书创建
              </div>
              <p className="text-xs text-text-tertiary mt-1">
                上传完整企划 → 多阶段 LLM 分析 → 确认后才创建正式书（不会先建空项目）
              </p>
            </button>
            <button className="panel p-4 text-left hover:border-brand/40 transition-colors" onClick={() => setMode("blank")}>
              <div className="flex items-center gap-2 text-sm text-text-primary" style={{ fontWeight: 510 }}>
                <FileText size={15} className="text-brand-accent" />
                创建空白小说
              </div>
              <p className="text-xs text-text-tertiary mt-1">只创建最小 Book，随后在作品中补设定</p>
            </button>
          </div>
        )}

        {mode === "upload" && (
          <div className="space-y-3">
            <label className="panel flex flex-col items-center py-10 cursor-pointer border-dashed">
              <Upload size={22} className="text-text-disabled mb-2" />
              <span className="text-xs text-text-secondary">{file ? file.name : "拖拽或选择企划（txt/md/docx/pdf/rtf/csv/json/html/xml…）"}</span>
              <input
                type="file"
                className="hidden"
                accept=".txt,.md,.markdown,.docx,.pdf,.rtf,.csv,.tsv,.json,.jsonl,.html,.htm,.xml,.log,text/plain,application/pdf"
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </label>
            <div className="flex gap-2 justify-end">
              <button className="btn text-xs py-1.5 px-3" onClick={() => setMode("choose")}>
                返回
              </button>
              <button className="btn-primary text-xs py-1.5 px-3" disabled={!file || busy} onClick={upload}>
                {busy ? <Loader2 size={12} className="animate-spin" /> : null}
                上传并分析
              </button>
            </div>
          </div>
        )}

        {mode === "blank" && (
          <div className="space-y-3">
            <input
              className="w-full rounded-md border border-border bg-bg-canvas px-3 py-2 text-xs"
              placeholder="书名"
              value={blankTitle}
              onChange={(e) => setBlankTitle(e.target.value)}
            />
            <div className="flex gap-2 justify-end">
              <button className="btn text-xs py-1.5 px-3" onClick={() => setMode("choose")}>
                返回
              </button>
              <button className="btn-primary text-xs py-1.5 px-3" disabled={!blankTitle.trim() || busy} onClick={createBlank}>
                创建
              </button>
            </div>
          </div>
        )}

        {mode === "analyzing" && (
          <div className="space-y-3 py-6 text-center">
            <Loader2 size={22} className="animate-spin mx-auto text-brand-accent" />
            <div className="text-sm text-text-primary">正在多阶段分析企划…</div>
            <div className="text-2xs text-text-tertiary font-mono">
              状态 {session?.status || "analyzing"} · 步骤 {session?.current_step || "…"} · 进度{" "}
              {Math.round((session?.progress || 0) * 100)}%
            </div>
            <p className="text-2xs text-text-disabled px-6">
              分类 → 清洗 → 元数据 → 世界 → 人物 → 关系 → 大纲 → 剧情线 → 写作规则 → 一致性
            </p>
            {sessionId && (
              <button
                className="btn text-2xs py-1 px-2"
                onClick={async () => {
                  try {
                    await api.imports.analyze(sessionId);
                  } catch (e: any) {
                    setError(e?.message || String(e));
                  }
                }}
              >
                重新排队
              </button>
            )}
          </div>
        )}

        {mode === "preview" && preview && (
          <div className="space-y-3">
            <div className="text-xs text-text-tertiary">
              状态 <span className="text-text-primary font-mono">{preview.status}</span>
              {preview.status === "needs_human" && (
                <span className="ml-2 text-amber-300">有待确认冲突</span>
              )}
            </div>
            <label className="block text-2xs text-text-disabled mb-1">正式书名</label>
            <input
              className="w-full rounded-md border border-border bg-bg-canvas px-3 py-2 text-xs"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <div className="grid grid-cols-3 gap-2">
              {Object.entries(counts).map(([k, v]) => (
                <div key={k} className="panel p-2 text-center">
                  <div className="text-sm text-text-primary font-mono">{String(v)}</div>
                  <div className="text-2xs text-text-disabled">{k}</div>
                </div>
              ))}
            </div>
            {characters.length > 0 && (
              <div className="panel p-3 text-2xs text-text-secondary max-h-28 overflow-auto">
                <div className="text-text-disabled mb-1">人物预览</div>
                {characters.slice(0, 12).map((c: any) => (
                  <div key={c.temp_id || c.canonical_name}>
                    {c.canonical_name}
                    {c.role ? ` · ${c.role}` : ""}
                  </div>
                ))}
              </div>
            )}
            {locations.length > 0 && (
              <div className="panel p-3 text-2xs text-text-secondary max-h-20 overflow-auto">
                <div className="text-text-disabled mb-1">地点（{locations.length}）</div>
                {locations.slice(0, 12).map((l: any) => (
                  <span key={l.name} className="inline-block mr-2 mb-1">
                    {l.name}
                  </span>
                ))}
              </div>
            )}
            {chapters.length > 0 && (
              <div className="panel p-3 text-2xs text-text-secondary max-h-28 overflow-auto">
                <div className="text-text-disabled mb-1">章纲预览（{chapters.length}）</div>
                {chapters.slice(0, 10).map((c: any) => (
                  <div key={c.chapter_no}>
                    第{c.chapter_no}章 {c.title || c.goal || ""}
                  </div>
                ))}
              </div>
            )}
            <div className="panel p-3 text-2xs text-text-secondary space-y-1 max-h-32 overflow-auto">
              <div className="text-text-disabled">{preview.preview?.note}</div>
              {(preview.conflicts || []).map((c: any) => {
                const selected = conflictSelections[c.conflict_id] || c.selected_option_id || c.options?.[0]?.id || "";
                return (
                <div key={c.conflict_id} className="rounded border border-border/70 p-2 space-y-2">
                  <div className="flex items-start gap-2">
                    <span className={c.severity === "blocking" ? "text-red-300" : "text-amber-300/90"}>
                      [{c.severity}] {c.status}: {c.code} — {c.message}
                    </span>
                  </div>
                  {c.status === "open" && (c.options || []).length > 0 && (
                    <div className="flex items-center gap-2">
                      <select
                        className="flex-1 rounded border border-border bg-bg-canvas px-2 py-1 text-2xs text-text-secondary"
                        value={selected}
                        onChange={(e) => setConflictSelections((prev) => ({ ...prev, [c.conflict_id]: e.target.value }))}
                      >
                        {(c.options || []).map((option: any) => (
                          <option key={option.id} value={option.id}>{option.label || option.title || option.id}</option>
                        ))}
                      </select>
                      <button
                        className="btn text-2xs py-0.5 px-2"
                        disabled={busy || !selected}
                        onClick={async () => {
                          if (!sessionId || !selected) return;
                          setBusy(true);
                          try {
                            await api.imports.resolveConflict(sessionId, c.conflict_id, selected);
                            setPreview(await api.imports.preview(sessionId));
                          } catch (e: any) {
                            setError(e?.message || String(e));
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        {busy ? <Loader2 size={10} className="animate-spin" /> : "应用此选项"}
                      </button>
                    </div>
                  )}
                </div>
                );
              })}
            </div>
            <div className="flex flex-wrap gap-2 justify-end">
              <button className="btn text-xs py-1.5 px-3" onClick={onClose}>
                取消
              </button>
              {openWarnings.length > 0 && (
                <button className="btn text-xs py-1.5 px-3" disabled={busy} onClick={resolveWarnings}>
                  一键处理警告（{openWarnings.length}）
                </button>
              )}
              <button
                className="btn-primary text-xs py-1.5 px-3"
                disabled={busy || openBlocking.length > 0}
                onClick={commit}
                title={openBlocking.length ? "请先处理阻断冲突" : "将自动忽略/采用默认处理警告项"}
              >
                {busy ? <Loader2 size={12} className="animate-spin" /> : null}
                确认创建小说
              </button>
            </div>
          </div>
        )}

        {error && <p className="text-xs text-red-400">{error}</p>}
      </div>
    </div>
  );
}
