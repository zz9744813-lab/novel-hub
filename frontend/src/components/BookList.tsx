import { useStore } from "../store";
import { Book as BookIcon, Plus, ChevronRight, Search } from "lucide-react";
import clsx from "clsx";
import { useState, useMemo } from "react";

export function BookList({ onNewBook }: { onNewBook: () => void }) {
  const { books, selectedBookId, selectBook } = useStore();
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!search.trim()) return books;
    const q = search.toLowerCase();
    return books.filter(b => b.title.toLowerCase().includes(q));
  }, [books, search]);

  return (
    <div>
      {/* Header row */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <BookIcon size={14} className="text-text-disabled" />
          <h2 className="text-xs text-text-primary uppercase tracking-wider" style={{ fontWeight: 510 }}>项目列表</h2>
          <span className="text-2xs text-text-disabled font-mono">{books.length}</span>
        </div>
        <div className="flex items-center gap-2">
          {/* Search */}
          <div className="relative">
            <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-disabled" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索项目..."
              className="input pl-7 py-1.5 text-2xs w-40"
            />
          </div>
          <button onClick={onNewBook} className="btn text-2xs">
            <Plus size={12} /> 新建
          </button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="panel flex flex-col items-center py-16 text-text-tertiary">
          <BookIcon size={32} className="mb-3 opacity-20" />
          <p className="text-xs">{search ? "没有匹配的项目" : "暂无项目，点击「新建」创建"}</p>
        </div>
      ) : (
        <div className="space-y-1">
          {filtered.map((b) => {
            const active = selectedBookId === b.book_id;
            const progress = b.target_chapters ? (b.finalized_chapters || 0) / b.target_chapters : 0;
            return (
              <div
                key={b.book_id}
                onClick={() => selectBook(b.book_id)}
                className={clsx("row-item", active && "active")}
              >
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-text-primary truncate" style={{ fontWeight: 510 }}>{b.title}</div>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-2xs text-text-tertiary font-mono">
                      {b.finalized_chapters || 0}/{b.target_chapters || "?"} ch
                    </span>
                    <span className="text-text-disabled">·</span>
                    <span className="text-2xs text-text-tertiary font-mono">
                      {(b.finalized_words || 0).toLocaleString()} w
                    </span>
                    {/* Progress bar */}
                    {b.target_chapters ? (
                      <div className="progress-bar flex-1 max-w-[80px]">
                        <div
                          className={clsx("progress-fill", progress >= 1 ? "bg-success" : "bg-brand")}
                          style={{ width: `${Math.min(progress * 100, 100)}%` }}
                        />
                      </div>
                    ) : null}
                  </div>
                </div>
                <span className={clsx(
                  "badge text-2xs",
                  b.status === "active" ? "bg-brand-muted text-brand-accent" :
                  b.status === "completed" ? "bg-success-muted text-success" :
                  "bg-bg-surface text-text-tertiary border-border-standard"
                )}>
                  {b.status || "idle"}
                </span>
                <ChevronRight size={12} className="text-text-disabled" />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
