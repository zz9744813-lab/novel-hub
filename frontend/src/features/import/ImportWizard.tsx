import { useState } from "react";
import { api } from "../../api";
import { Loader2, Upload, FileText, Sparkles } from "lucide-react";

export function ImportWizard({
  onClose,
  onCommitted,
}: {
  onClose: () => void;
  onCommitted: (bookId: string) => void;
}) {
  const [mode, setMode] = useState<"choose" | "upload" | "blank" | "preview">("choose");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [title, setTitle] = useState("");
  const [blankTitle, setBlankTitle] = useState("");

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.imports.create(file);
      setSessionId(r.import_session_id);
      const p = await api.imports.preview(r.import_session_id);
      setPreview(p);
      setTitle(p.preview?.title_guess || file.name.replace(/\.[^.]+$/, ""));
      setMode("preview");
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
      // resolve open warning conflicts as review_later
      for (const c of preview.conflicts || []) {
        if (c.status === "open" && c.options?.[0]?.id) {
          await api.imports.resolveConflict(sessionId, c.conflict_id, c.options[0].id);
        }
      }
      const r = await api.imports.commit(sessionId, {
        expected_preview_hash: preview.preview_hash,
        book_overrides: { title: title || preview.preview?.title_guess },
      });
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="bg-bg-elevated border border-border rounded-lg w-full max-w-lg max-h-[85vh] overflow-auto p-5 space-y-4"
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
            <button
              className="panel p-4 text-left hover:border-brand/40 transition-colors"
              onClick={() => setMode("upload")}
            >
              <div className="flex items-center gap-2 text-sm text-text-primary" style={{ fontWeight: 510 }}>
                <Sparkles size={15} className="text-brand-accent" />
                从企划书创建
              </div>
              <p className="text-xs text-text-tertiary mt-1">
                上传完整企划 → 提取预览 → 确认后才创建正式书（不会先建空项目）
              </p>
            </button>
            <button
              className="panel p-4 text-left hover:border-brand/40 transition-colors"
              onClick={() => setMode("blank")}
            >
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
              <span className="text-xs text-text-secondary">{file ? file.name : "选择企划文件（docx/pdf/txt/md…）"}</span>
              <input
                type="file"
                className="hidden"
                accept=".txt,.md,.docx,.pdf,.html,.json,.csv"
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

        {mode === "preview" && preview && (
          <div className="space-y-3">
            <div className="text-xs text-text-tertiary">
              状态 <span className="text-text-primary font-mono">{preview.status}</span> · 预览哈希{" "}
              <span className="font-mono">{(preview.preview_hash || "").slice(0, 12)}…</span>
            </div>
            <label className="block text-2xs text-text-disabled mb-1">正式书名</label>
            <input
              className="w-full rounded-md border border-border bg-bg-canvas px-3 py-2 text-xs"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
            <div className="panel p-3 text-2xs text-text-secondary space-y-1 max-h-40 overflow-auto">
              <div>块数：{preview.preview?.block_count}</div>
              <div>标题样例：{(preview.preview?.headings_sample || []).slice(0, 8).join(" · ")}</div>
              <div className="text-text-disabled">{preview.preview?.note}</div>
              {(preview.conflicts || []).map((c: any) => (
                <div key={c.conflict_id} className="text-amber-300/90">
                  冲突 {c.code}: {c.message}
                </div>
              ))}
            </div>
            <div className="flex gap-2 justify-end">
              <button className="btn text-xs py-1.5 px-3" onClick={onClose}>
                取消
              </button>
              <button className="btn-primary text-xs py-1.5 px-3" disabled={busy} onClick={commit}>
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
