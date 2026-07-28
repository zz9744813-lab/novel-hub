import { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { BookCard } from "./BookCard";
import { BookshelfBook, LIFECYCLE_LABEL } from "./library.types";
import { LibraryEmptyState } from "./LibraryEmptyState";
import { Loader2, Plus, Search, LayoutGrid } from "lucide-react";

export function LibraryPage({
  onOpenBook,
  onNewBook,
}: {
  onOpenBook: (bookId: string) => void;
  onNewBook: () => void;
}) {
  const [books, setBooks] = useState<BookshelfBook[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<string>("all");
  const [sort, setSort] = useState<"updated" | "title" | "progress" | "words">("updated");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.library.books();
      setBooks(data.books || []);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filtered = useMemo(() => {
    let list = [...books];
    if (filter !== "all") {
      list = list.filter((b) => b.lifecycle_status === filter);
    }
    if (q.trim()) {
      const s = q.trim().toLowerCase();
      list = list.filter(
        (b) =>
          (b.title || "").toLowerCase().includes(s) ||
          (b.tags || []).some((t) => t.toLowerCase().includes(s)) ||
          (b.logline || "").toLowerCase().includes(s)
      );
    }
    list.sort((a, b) => {
      if (sort === "title") return (a.title || "").localeCompare(b.title || "", "zh");
      if (sort === "words") return (b.finalized_words || 0) - (a.finalized_words || 0);
      if (sort === "progress") {
        const pa = (a.finalized_chapters || 0) / Math.max(a.planned_chapters || 1, 1);
        const pb = (b.finalized_chapters || 0) / Math.max(b.planned_chapters || 1, 1);
        return pb - pa;
      }
      return (b.updated_at || "").localeCompare(a.updated_at || "");
    });
    return list;
  }, [books, q, filter, sort]);

  return (
    <div className="h-full overflow-auto p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3 mb-5">
        <div>
          <h1 className="text-base text-text-primary" style={{ fontWeight: 510 }}>
            我的书架
          </h1>
          <p className="text-xs text-text-tertiary mt-0.5">
            小说卡片 · 进度 · 风险 · 正在进行的任务
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <div className="relative">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-disabled" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索书名 / 标签"
              className="pl-7 pr-3 py-1.5 text-xs rounded-md border border-border bg-bg-canvas text-text-primary w-44"
            />
          </div>
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="text-xs rounded-md border border-border bg-bg-canvas text-text-secondary px-2 py-1.5"
          >
            <option value="all">全部状态</option>
            {Object.entries(LIFECYCLE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as any)}
            className="text-xs rounded-md border border-border bg-bg-canvas text-text-secondary px-2 py-1.5"
          >
            <option value="updated">最近活动</option>
            <option value="title">书名</option>
            <option value="progress">进度</option>
            <option value="words">字数</option>
          </select>
          <button onClick={onNewBook} className="btn-primary text-xs py-1.5 px-3 flex items-center gap-1.5">
            <Plus size={13} />
            新建小说
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-3 text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-24 text-text-tertiary text-xs gap-2">
          <Loader2 size={16} className="animate-spin" /> 加载书架…
        </div>
      ) : filtered.length === 0 ? (
        <LibraryEmptyState onNew={onNewBook} />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filtered.map((b) => (
            <BookCard key={b.book_id} book={b} onOpen={onOpenBook} />
          ))}
        </div>
      )}

      <div className="mt-6 flex items-center gap-2 text-2xs text-text-disabled">
        <LayoutGrid size={11} />
        {filtered.length} 本 · 数据来自 GET /api/library/books
      </div>
    </div>
  );
}
