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
        className="bg-ink-900 rounded-2xl p-8 w-full max-w-md border border-ink-700 shadow-2xl animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-lg bg-ink-850 flex items-center justify-center">
            <PenTool size={18} className="text-accent" />
          </div>
          <div>
            <h3 className="text-lg font-medium text-ink-200">新建小说项目</h3>
            <p className="text-xs text-ink-500">输入基本信息，开始锻造</p>
          </div>
          <button onClick={onClose} className="ml-auto text-ink-500 hover:text-ink-300">
            <X size={18} />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-xs text-ink-400 mb-1.5">书名 *</label>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="输入小说标题"
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
              className="w-full px-3 py-2.5 bg-ink-950 border border-ink-700 rounded-lg text-sm text-ink-200 placeholder-ink-600 focus-accent transition-all"
            />
          </div>

          <div>
            <label className="block text-xs text-ink-400 mb-1.5">描述（可选）</label>
            <textarea
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="一句话概括故事内核..."
              className="w-full px-3 py-2.5 h-20 bg-ink-950 border border-ink-700 rounded-lg text-sm text-ink-200 placeholder-ink-600 resize-none focus-accent transition-all"
            />
          </div>

          <div>
            <label className="block text-xs text-ink-400 mb-1.5">目标章节数</label>
            <input
              type="number"
              value={chapters}
              onChange={(e) => setChapters(parseInt(e.target.value) || 500)}
              className="w-full px-3 py-2.5 bg-ink-950 border border-ink-700 rounded-lg text-sm text-ink-200 focus-accent transition-all"
            />
          </div>

          {error && <div className="text-sm text-red-400">{error}</div>}

          <button
            onClick={handleCreate}
            disabled={loading || !title.trim()}
            className="w-full py-2.5 rounded-lg bg-accent text-ink-950 text-sm font-medium hover:bg-accent-bright disabled:opacity-40 disabled:cursor-not-allowed transition-all flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : null}
            {loading ? "创建中..." : "创建项目"}
          </button>
        </div>
      </div>
    </div>
  );
}
