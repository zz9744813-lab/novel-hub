import { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { BookCard } from "./BookCard";
import { BookshelfBook, LIFECYCLE_LABEL } from "./library.types";
import { LibraryEmptyState } from "./LibraryEmptyState";
import { BookOpen, Filter, LayoutGrid, Loader2, Plus, Search, X, ArrowUpRight, BookText, FileText, ListChecks } from "lucide-react";
import { FluidBackground } from "../../components/FluidBackground";

function formatWords(value: number): string {
  if (value >= 10000) return `${(value / 10000).toFixed(1)} 万`;
  return value.toLocaleString("zh-CN");
}

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
  const [deletingBook, setDeletingBook] = useState<BookshelfBook | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [selectedBook, setSelectedBook] = useState<BookshelfBook | null>(null);

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

  const handleDelete = async () => {
    if (!deletingBook) return;
    setDeleteBusy(true);
    setError(null);
    try {
      await api.books.delete(deletingBook.book_id);
      setBooks((prev) => prev.filter((book) => book.book_id !== deletingBook.book_id));
      setDeletingBook(null);
      if (selectedBook?.book_id === deletingBook.book_id) setSelectedBook(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setDeleteBusy(false);
    }
  };

  const filtered = useMemo(() => {
    let list = [...books];
    if (filter !== "all") list = list.filter((book) => book.lifecycle_status === filter);
    if (q.trim()) {
      const search = q.trim().toLowerCase();
      list = list.filter(
        (book) =>
          (book.title || "").toLowerCase().includes(search) ||
          (book.tags || []).some((tag) => tag.toLowerCase().includes(search)) ||
          (book.logline || "").toLowerCase().includes(search)
      );
    }
    list.sort((a, b) => {
      if (sort === "title") return (a.title || "").localeCompare(b.title || "", "zh");
      if (sort === "words") return (b.finalized_words || 0) - (a.finalized_words || 0);
      if (sort === "progress") {
        return (
          (b.finalized_chapters || 0) / Math.max(b.planned_chapters || 1, 1) -
          (a.finalized_chapters || 0) / Math.max(a.planned_chapters || 1, 1)
        );
      }
      return (b.updated_at || "").localeCompare(a.updated_at || "");
    });
    return list;
  }, [books, q, filter, sort]);

  const totalWords = books.reduce((sum, book) => sum + (book.finalized_words || 0), 0);
  const activeCount = books.filter((book) => ["writing", "importing"].includes(book.lifecycle_status)).length;
  const riskCount = books.reduce((sum, book) => sum + (book.unresolved_risk_count || 0), 0);

  return (
    <div className="library-page h-full overflow-auto">
      <FluidBackground />
      <div className="library-page-inner">
        {/* ── 左主右辅工作台头部 ── */}
        <header className="library-workspace-head">
          <div className="library-workspace-primary">
            <div className="library-eyebrow">
              <BookOpen size={13} /> 创作档案 · 书架
            </div>
            <h1>我的书架</h1>
            <p>把正在写的、准备写的和已经写完的故事，放回它们应该在的位置。</p>
          </div>
          <div className="library-workspace-actions">
            <button onClick={onNewBook} className="btn-primary library-new-book">
              <Plus size={14} /> 新建作品
            </button>
          </div>
          <div className="library-workspace-stats">
            <div className="ws-stat">
              <span className="ws-stat-label">作品</span>
              <strong className="ws-stat-value">{books.length}</strong>
            </div>
            <div className="ws-stat">
              <span className="ws-stat-label">字数</span>
              <strong className="ws-stat-value">{formatWords(totalWords)}</strong>
            </div>
            <div className="ws-stat">
              <span className="ws-stat-label">进行中</span>
              <strong className="ws-stat-value">{activeCount}</strong>
            </div>
            <div className={`ws-stat${riskCount ? " has-risk" : ""}`}>
              <span className="ws-stat-label">风险</span>
              <strong className="ws-stat-value">{riskCount}</strong>
            </div>
          </div>
        </header>

        {/* ── 紧凑控制条 ── */}
        <section className="library-control-bar" aria-label="筛选和排序">
          <div className="library-search">
            <Search size={13} />
            <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="查找作品、标签或梗概" />
            {q && (
              <button type="button" aria-label="清空搜索" onClick={() => setQ("")}>
                <X size={12} />
              </button>
            )}
          </div>
          <div className="library-control-group">
            <div className="library-control-select">
              <Filter size={12} />
              <select value={filter} onChange={(event) => setFilter(event.target.value)}>
                <option value="all">全部作品</option>
                {Object.entries(LIFECYCLE_LABEL).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
            <select
              className="library-control-sort"
              value={sort}
              onChange={(event) => setSort(event.target.value as typeof sort)}
            >
              <option value="updated">最近活动</option>
              <option value="title">按书名</option>
              <option value="progress">按进度</option>
              <option value="words">按字数</option>
            </select>
            <span className="library-control-count">
              {filtered.length}/{books.length}
            </span>
          </div>
        </section>

        {error && (
          <div className="library-error">
            <span>{error}</span>
            <button type="button" onClick={load}>
              重试
            </button>
          </div>
        )}

        {loading ? (
          <div className="library-loading">
            <Loader2 size={16} className="animate-spin" /> 正在整理书架…
          </div>
        ) : filtered.length === 0 ? (
          <LibraryEmptyState onNew={onNewBook} />
        ) : (
          <section className="library-room" aria-label="作品书架">
            <div className="library-shelf-stack">
              {filtered.map((book) => (
                <BookCard
                  key={book.book_id}
                  book={book}
                  onOpen={onOpenBook}
                  onDelete={setDeletingBook}
                  isSelected={selectedBook?.book_id === book.book_id}
                  onSelect={setSelectedBook}
                />
              ))}
              <button type="button" className="shelf-add-book" onClick={onNewBook}>
                <span>
                  <Plus size={20} />
                </span>
                <strong>放入新作品</strong>
                <small>从企划或空白稿开始</small>
              </button>
            </div>
          </section>
        )}

        {/* ── 选中作品工作台 ── */}
        {selectedBook && (
          <section className="library-selected-workspace" aria-label="选中作品操作区">
            <div className="lsw-bar">
              <span className="lsw-indicator" aria-hidden="true" />
              <span className="lsw-title">{selectedBook.title}</span>
              <span className="lsw-meta">
                {selectedBook.finalized_chapters}/{selectedBook.planned_chapters ?? "?"} 章 · {formatWords(selectedBook.finalized_words || 0)} 字
              </span>
              <div className="lsw-actions">
                <button
                  type="button"
                  className="lsw-action primary"
                  onClick={() => onOpenBook(selectedBook.book_id)}
                >
                  <ArrowUpRight size={13} /> 打开作品
                </button>
                <button
                  type="button"
                  className="lsw-action"
                  title="查看章节"
                  onClick={() => onOpenBook(selectedBook.book_id)}
                >
                  <FileText size={13} /> 章节
                </button>
                <button
                  type="button"
                  className="lsw-action"
                  title="查看大纲"
                >
                  <ListChecks size={13} /> 大纲
                </button>
                <button
                  type="button"
                  className="lsw-action"
                  title="查看设定"
                >
                  <BookText size={13} /> 设定
                </button>
              </div>
            </div>
          </section>
        )}

        <footer className="library-footer">
          <LayoutGrid size={12} /> 书架数据实时来自 NovelForge 工作台
        </footer>
      </div>

      {deletingBook && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
          onClick={() => !deleteBusy && setDeletingBook(null)}
        >
          <div
            className="w-full max-w-sm rounded-xl border border-border bg-bg-panel p-5 shadow-2xl animate-modal-in"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 className="text-sm text-text-primary font-medium">移出这本书</h2>
            <p className="mt-2 text-xs text-text-secondary leading-5">
              确定要删除《{deletingBook.title}》吗？作品、章节、设定、任务和封面都会被移除，且无法撤销。
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn text-xs px-3 py-1.5" disabled={deleteBusy} onClick={() => setDeletingBook(null)}>
                取消
              </button>
              <button
                className="btn-danger text-xs px-3 py-1.5 flex items-center gap-1.5"
                disabled={deleteBusy}
                onClick={handleDelete}
              >
                {deleteBusy && <Loader2 size={12} className="animate-spin" />}
                {deleteBusy ? "删除中…" : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}