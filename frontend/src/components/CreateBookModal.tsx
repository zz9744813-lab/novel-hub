import { useState, useRef, useCallback } from "react";
import { useStore } from "../store";
import { api } from "../api";
import { X, Loader2, PenTool, Upload, FileText, CheckCircle2 } from "lucide-react";

const ACCEPTED = ".txt,.md,.markdown,.text,.docx,.doc,.pdf,.rtf,.csv,.tsv,.json,.jsonl,.html,.htm,.xml,.log";
const MAX_MB = 5;
const ACCEPT_HINT = ".txt / .md / .docx / .pdf / .rtf / .csv / .json / .html";

export function CreateBookModal({ onClose }: { onClose: () => void }) {
  const { createBook } = useStore();
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [chapters, setChapters] = useState(500);
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState<"form" | "uploading" | "parsing" | "done">("form");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const pickFile = useCallback((f: File | null | undefined) => {
    if (!f) return;
    const name = f.name.toLowerCase();
    const ok = [
      ".txt", ".md", ".markdown", ".text",
      ".docx", ".doc", ".pdf", ".rtf",
      ".csv", ".tsv", ".json", ".jsonl",
      ".html", ".htm", ".xml", ".log",
    ].some((ext) => name.endsWith(ext));
    if (!ok) {
      setError(`不支持该文件类型。支持：${ACCEPT_HINT}`);
      return;
    }
    if (f.size > MAX_MB * 1024 * 1024) {
      setError(`文件过大，上限 ${MAX_MB}MB`);
      return;
    }
    setError(null);
    setFile(f);
    if (!title.trim()) {
      const base = f.name.replace(/\.[^.]+$/i, "");
      setTitle(base);
    }
  }, [title]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    pickFile(e.dataTransfer.files?.[0]);
  };

  const handleCreate = async () => {
    if (!title.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);

    try {
      // 1. Create book
      setStep("form");
      const id = await createBook(title.trim(), desc || undefined, chapters);
      if (!id) {
        setError("创建失败，请检查标题");
        setLoading(false);
        return;
      }

      // 2. Optional: upload outline file
      if (file) {
        setStep("uploading");
        const up = await api.outlines.upload(id, file, chapters);
        if (up.status === "error" || (up.errors && up.errors.length > 0)) {
          setResult(`项目已创建，但大纲解析有问题：${(up.errors || []).join("; ") || up.status}`);
        } else {
          setStep("parsing");
          setResult(`项目已创建 · 大纲已上传（${up.filename || file.name}，${up.chars || "?"} 字）· 版本 v${up.version ?? "—"} · ${up.status}`);
        }
      } else {
        setResult("项目已创建（未上传大纲，可稍后在大纲页上传）");
      }

      setStep("done");
      // Auto close after short delay so user sees success
      setTimeout(() => onClose(), 1200);
    } catch (e: any) {
      setError(e.message || "创建失败");
      setStep("form");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-bg-canvas/70 flex items-center justify-center z-50 p-6"
      onClick={onClose}
    >
      <div
        className="panel-elevated p-6 w-full max-w-md animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-2 mb-5">
          <PenTool size={14} className="text-brand-accent" />
          <h3 className="text-xs text-text-primary uppercase tracking-wider" style={{ fontWeight: 510 }}>新建项目</h3>
          <button onClick={onClose} className="btn-ghost p-1 ml-auto">
            <X size={14} />
          </button>
        </div>

        <div className="space-y-3">
          {/* Drop zone */}
          <div>
            <label className="block text-2xs text-text-tertiary mb-1.5 uppercase tracking-wider">
              大纲文件 <span className="text-text-disabled normal-case">（可选 · {ACCEPT_HINT}）</span>
            </label>
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              className={`
                relative border border-dashed rounded-lg px-4 py-6 cursor-pointer transition-all duration-150
                flex flex-col items-center justify-center gap-2 text-center
                ${dragOver
                  ? "border-brand bg-brand-muted"
                  : file
                  ? "border-brand/40 bg-bg-elevated"
                  : "border-border hover:border-border-strong hover:bg-bg-hover"
                }
              `}
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED}
                className="hidden"
                onChange={(e) => pickFile(e.target.files?.[0])}
              />
              {file ? (
                <>
                  <FileText size={20} className="text-brand-accent" />
                  <div className="text-xs text-text-primary" style={{ fontWeight: 510 }}>{file.name}</div>
                  <div className="text-2xs font-mono text-text-disabled">
                    {(file.size / 1024).toFixed(1)} KB · 点击更换
                  </div>
                </>
              ) : (
                <>
                  <Upload size={18} className={dragOver ? "text-brand-accent" : "text-text-disabled"} />
                  <div className="text-xs text-text-secondary">
                    {dragOver ? "松开以上传" : "拖拽大纲文件到这里"}
                  </div>
                  <div className="text-2xs text-text-disabled">或点击选择 · 最大 {MAX_MB}MB · {ACCEPT_HINT}</div>
                </>
              )}
            </div>
            {file && (
              <button
                type="button"
                className="mt-1.5 text-2xs text-text-disabled hover:text-text-tertiary"
                onClick={(e) => { e.stopPropagation(); setFile(null); }}
              >
                移除文件
              </button>
            )}
          </div>

          <div>
            <label className="block text-2xs text-text-tertiary mb-1 uppercase tracking-wider">书名 *</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="输入小说标题（选文件后可自动填充）"
              onKeyDown={(e) => e.key === "Enter" && !loading && handleCreate()}
              className="input"
            />
          </div>

          <div>
            <label className="block text-2xs text-text-tertiary mb-1 uppercase tracking-wider">描述</label>
            <textarea
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="一句话概括故事内核..."
              className="input h-16 resize-none"
            />
          </div>

          <div>
            <label className="block text-2xs text-text-tertiary mb-1 uppercase tracking-wider">目标章节数</label>
            <input
              type="number"
              value={chapters}
              onChange={(e) => setChapters(parseInt(e.target.value) || 500)}
              className="input font-mono"
            />
          </div>

          {error && <div className="text-2xs text-danger bg-red-400/10 border border-red-400/20 rounded px-2 py-1.5">{error}</div>}
          {result && (
            <div className="text-2xs text-emerald-400 bg-emerald-400/10 border border-emerald-400/20 rounded px-2 py-1.5 flex items-start gap-1.5">
              <CheckCircle2 size={12} className="mt-0.5 shrink-0" />
              <span>{result}</span>
            </div>
          )}

          <button
            onClick={handleCreate}
            disabled={loading || !title.trim()}
            className="btn-primary w-full py-2 text-xs rounded-md justify-center gap-1.5"
          >
            {loading && <Loader2 size={12} className="animate-spin" />}
            {!loading && file && <Upload size={12} />}
            {loading
              ? step === "uploading"
                ? "上传大纲中..."
                : step === "parsing"
                ? "解析大纲中..."
                : "创建中..."
              : file
              ? "创建并上传大纲"
              : "创建项目"}
          </button>

          <p className="text-2xs text-text-disabled text-center">
            上传后走 AI 大纲解析 · 也可先建空项目再在「大纲依赖」页上传
          </p>
        </div>
      </div>
    </div>
  );
}
