import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { api } from "../../api";
import { BookCard } from "./BookCard";
import { BookshelfBook, LIFECYCLE_LABEL } from "./library.types";
import { LibraryEmptyState } from "./LibraryEmptyState";
import { ArrowRight, BookOpen, Filter, LayoutGrid, Loader2, Plus, Search, Trash2, X } from "lucide-react";

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
  const [previewBook, setPreviewBook] = useState<BookshelfBook | null>(null);
  const [deletingBook, setDeletingBook] = useState<BookshelfBook | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const shelfRef = useRef<HTMLDivElement>(null);
  const [shelfColumns, setShelfColumns] = useState(1);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.library.books();
      const nextBooks = data.books || [];
      setBooks(nextBooks);
      setPreviewBook((current) => current ? nextBooks.find((book) => book.book_id === current.book_id) || nextBooks[0] || null : nextBooks[0] || null);
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
      setPreviewBook((current) => current?.book_id === deletingBook.book_id ? null : current);
      setDeletingBook(null);
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
      list = list.filter((book) =>
        (book.title || "").toLowerCase().includes(search) ||
        (book.tags || []).some((tag) => tag.toLowerCase().includes(search)) ||
        (book.logline || "").toLowerCase().includes(search)
      );
    }
    list.sort((a, b) => {
      if (sort === "title") return (a.title || "").localeCompare(b.title || "", "zh");
      if (sort === "words") return (b.finalized_words || 0) - (a.finalized_words || 0);
      if (sort === "progress") {
        return (b.finalized_chapters || 0) / Math.max(b.planned_chapters || 1, 1) - (a.finalized_chapters || 0) / Math.max(a.planned_chapters || 1, 1);
      }
      return (b.updated_at || "").localeCompare(a.updated_at || "");
    });
    return list;
  }, [books, q, filter, sort]);

  const totalWords = books.reduce((sum, book) => sum + (book.finalized_words || 0), 0);
  const activeCount = books.filter((book) => ["writing", "importing"].includes(book.lifecycle_status)).length;
  const riskCount = books.reduce((sum, book) => sum + (book.unresolved_risk_count || 0), 0);

  useEffect(() => {
    const node = shelfRef.current;
    if (!node) return;
    const updateColumns = () => {
      const width = node.clientWidth;
      setShelfColumns(Math.max(1, Math.min(filtered.length + 1, Math.floor((width + 48) / 180))));
    };
    updateColumns();
    const observer = new ResizeObserver(updateColumns);
    observer.observe(node);
    return () => observer.disconnect();
  }, [filtered.length]);

  const shelfRows = useMemo(() => {
    const rows: BookshelfBook[][] = [];
    for (let i = 0; i < filtered.length; i += shelfColumns) rows.push(filtered.slice(i, i + shelfColumns));
    return rows;
  }, [filtered, shelfColumns]);

  return (
    <div className="library-page h-full overflow-auto">
      <div className="library-page-inner">
        <header className="library-heading">
          <div>
            <div className="library-eyebrow"><BookOpen size={13} /> 创作档案 · 书架</div>
            <h1>我的书架</h1>
            <p>把正在写的、准备写的和已经写完的故事，放回它们应该在的位置。</p>
          </div>
          <button onClick={onNewBook} className="btn-primary library-new-book"><Plus size={14} /> 新建作品</button>
        </header>

        <section className="library-stats" aria-label="书架概览">
          <div><span>作品</span><strong>{books.length}</strong><small>本</small></div>
          <div><span>已完成字数</span><strong>{formatWords(totalWords)}</strong><small>字</small></div>
          <div><span>正在创作</span><strong>{activeCount}</strong><small>本</small></div>
          <div className={riskCount ? "has-risk" : ""}><span>待处理风险</span><strong>{riskCount}</strong><small>项</small></div>
        </section>

        <section className="library-toolbar" aria-label="筛选和排序">
          <div className="library-search">
            <Search size={14} />
            <input value={q} onChange={(event) => setQ(event.target.value)} placeholder="查找作品、标签或梗概" />
            {q && <button type="button" aria-label="清空搜索" onClick={() => setQ("")}><X size={13} /></button>}
          </div>
          <div className="library-toolbar-select"><Filter size={13} /><select value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">全部作品</option>{Object.entries(LIFECYCLE_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></div>
          <select className="library-sort" value={sort} onChange={(event) => setSort(event.target.value as typeof sort)}><option value="updated">最近活动</option><option value="title">按书名</option><option value="progress">按进度</option><option value="words">按字数</option></select>
          <span className="library-result-count">显示 {filtered.length} / {books.length}</span>
        </section>

        {error && <div className="library-error"><span>{error}</span><button type="button" onClick={load}>重试</button></div>}

        {loading ? (
          <div className="library-loading"><Loader2 size={18} className="animate-spin" /> 正在整理书架…</div>
        ) : filtered.length === 0 ? (
          <LibraryEmptyState onNew={onNewBook} />
        ) : (
          <section className="library-room" aria-label="作品书架" ref={shelfRef}>
            <div className="library-room-light" aria-hidden="true" />
            <div className="library-shelf-stack">
              {shelfRows.map((row, rowIndex) => (
                <div className="library-shelf-level" key={`shelf-${rowIndex}`}>
                  <div className="library-shelf-row" style={{ gridTemplateColumns: `repeat(${Math.max(row.length, 1)}, minmax(0, 1fr))` } as CSSProperties}>
                    {row.map((book) => (
                      <BookCard key={book.book_id} book={book} selected={previewBook?.book_id === book.book_id} onOpen={onOpenBook} onPreview={setPreviewBook} onDelete={setDeletingBook} />
                    ))}
                  </div>
                  <div className="library-shelf-board" aria-hidden="true"><span /><span /></div>
                </div>
              ))}
              <div className="library-shelf-level library-shelf-add-level">
                <div className="library-shelf-row" style={{ gridTemplateColumns: `repeat(${Math.max(1, Math.min(shelfColumns, 3))}, minmax(0, 1fr))` } as CSSProperties}>
                  <button type="button" className="shelf-add-book" onClick={onNewBook}><span><Plus size={20} /></span><strong>放入新作品</strong><small>从企划或空白稿开始</small></button>
                </div>
                <div className="library-shelf-board" aria-hidden="true"><span /><span /></div>
              </div>
            </div>
          </section>
        )}

        {previewBook && !loading && (
          <aside className="book-preview-panel">
            <div className="book-preview-mark"><span /> 当前选中</div>
            <div className="book-preview-content">
              <div className="book-preview-copy">
                <div className="book-preview-status">{LIFECYCLE_LABEL[previewBook.lifecycle_status] || previewBook.lifecycle_status}</div>
                <h2>{previewBook.title}</h2>
                <p>{previewBook.logline || "还没有写下梗概。从第一章开始，让这个故事拥有自己的方向。"}</p>
                <div className="book-preview-metrics"><span><strong>{previewBook.finalized_chapters}</strong> / {previewBook.planned_chapters ?? "?"}<small>章节</small></span><span><strong>{formatWords(previewBook.finalized_words || 0)}</strong><small>已完成字数</small></span><span><strong>{previewBook.unresolved_risk_count || 0}</strong><small>待处理</small></span></div>
              </div>
              <div className="book-preview-actions"><button type="button" className="btn-primary" onClick={() => onOpenBook(previewBook.book_id)}><BookOpen size={14} /> 翻开这本书 <ArrowRight size={14} /></button><button type="button" className="btn" onClick={() => setDeletingBook(previewBook)}><Trash2 size={13} /> 删除</button></div>
            </div>
          </aside>
        )}

        <footer className="library-footer"><LayoutGrid size={12} /> 书架数据实时来自 NovelForge 工作台</footer>
      </div>

      {deletingBook && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={() => !deleteBusy && setDeletingBook(null)}>
          <div className="w-full max-w-sm rounded-lg border border-border bg-bg-panel p-5 shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <h2 className="text-sm text-text-primary font-medium">移出这本书</h2>
            <p className="mt-2 text-xs text-text-secondary leading-5">确定要删除《{deletingBook.title}》吗？作品、章节、设定、任务和封面都会被移除，且无法撤销。</p>
            <div className="mt-5 flex justify-end gap-2"><button className="btn text-xs px-3 py-1.5" disabled={deleteBusy} onClick={() => setDeletingBook(null)}>取消</button><button className="btn-danger text-xs px-3 py-1.5 flex items-center gap-1.5" disabled={deleteBusy} onClick={handleDelete}>{deleteBusy && <Loader2 size={12} className="animate-spin" />}{deleteBusy ? "删除中…" : "确认删除"}</button></div>
          </div>
        </div>
      )}
    </div>
  );
}
