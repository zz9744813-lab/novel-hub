import { useState } from "react";
import { useStore } from "../store";
import { X, Loader2, PenTool } from "lucide-react";

export function CreateBookModal({ onClose }: { onClose: () => void }) {
  const { createBook } = useStore();
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [chapters, setChapters] = useState(500);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!title.trim()) return;
    setLoading(true);
    setError(null);
    const id = await createBook(title, desc);
    setLoading(false);
    if (id) onClose();
    else setError("创建失败，请检查标题");
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-6"
      onClick={onClose}
    >
      <div
        className="panel-elevated p-6 w-full max-w-sm animate-slide-up"
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
          <div>
            <label className="block text-2xs text-text-tertiary mb-1 uppercase tracking-wider">书名 *</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="输入小说标题"
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
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

          {error && <div className="text-2xs text-danger">{error}</div>}

          <button
            onClick={handleCreate}
            disabled={loading || !title.trim()}
            className="btn-primary w-full py-2 text-xs rounded-md justify-center"
          >
            {loading && <Loader2 size={12} className="animate-spin" />}
            {loading ? "创建中..." : "创建项目"}
          </button>
        </div>
      </div>
    </div>
  );
}
