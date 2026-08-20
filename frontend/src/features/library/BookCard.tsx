import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import clsx from "clsx";
import { AlertTriangle, ArrowUpRight, BookOpen, Trash2 } from "lucide-react";
import { fetchAuthenticatedAsset } from "../../api";
import { BookshelfBook, LIFECYCLE_LABEL } from "./library.types";

function progressPct(book: BookshelfBook): number {
  const planned = book.planned_chapters || 0;
  if (!planned) return book.finalized_chapters > 0 ? 5 : 0;
  return Math.min(100, Math.round((book.finalized_chapters / planned) * 100));
}

function statusTone(status: string): string {
  if (status === "writing") return "is-writing";
  if (status === "needs_human") return "is-warning";
  if (status === "completed") return "is-complete";
  return "is-quiet";
}

export function BookCard({
  book,
  onOpen,
  onDelete,
  isSelected,
  onSelect,
}: {
  book: BookshelfBook;
  onOpen: (id: string) => void;
  onDelete: (book: BookshelfBook) => void;
  isSelected?: boolean;
  onSelect?: (book: BookshelfBook | null) => void;
}) {
  const [coverError, setCoverError] = useState(false);
  const [coverSrc, setCoverSrc] = useState<string | null>(null);
  const pct = progressPct(book);
  const style = book.cover_style || {
    background: "linear-gradient(145deg, #2c314b 0%, #151923 72%)",
    accent: "#9ca8ff",
    bg: "#202538",
  };
  const showCoverImage = Boolean(coverSrc) && !coverError;
  const cssVars = {
    ["--book-background" as string]: style.background,
    ["--book-accent" as string]: style.accent || "#9ca8ff",
  } as CSSProperties;

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setCoverError(false);
    setCoverSrc(null);
    if (!book.cover_url) return () => undefined;
    fetchAuthenticatedAsset(book.cover_url)
      .then((url) => {
        objectUrl = url;
        if (active) setCoverSrc(url);
      })
      .catch(() => {
        if (active) setCoverError(true);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [book.cover_url]);

  const handleClick = () => {
    if (onSelect) {
      onSelect(book);
    } else {
      onOpen(book.book_id);
    }
  };

  const handleDoubleClick = () => {
    onOpen(book.book_id);
  };

  return (
    <article
      className={clsx("shelf-book", isSelected && "is-selected")}
      style={cssVars}
    >
      <div className="shelf-book-shadow" aria-hidden="true" />
      <button
        type="button"
        className="shelf-book-button"
        aria-label={`打开《${book.title}》`}
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
      >
        <span className="shelf-book-pages" aria-hidden="true">
          <span className="shelf-book-page-lines" />
        </span>
        <span className="shelf-book-spine" aria-hidden="true">
          <span className="shelf-book-spine-title">{book.title}</span>
          <span className="shelf-book-spine-brand" aria-hidden="true" />
        </span>
        <span className="shelf-book-cover">
          {showCoverImage && (
            <img
              src={coverSrc || undefined}
              alt=""
              className="shelf-book-cover-image"
              onError={() => setCoverError(true)}
            />
          )}
          <span className="shelf-book-cover-shade" aria-hidden="true" />
          <span className="shelf-book-cover-frame" aria-hidden="true" />
          <span className="shelf-book-cover-kicker">NOVELFORGE · {book.genre || "长篇小说"}</span>
          <span className="shelf-book-cover-title">{book.title}</span>
          {book.subtitle && <span className="shelf-book-cover-subtitle">{book.subtitle}</span>}
          <span className="shelf-book-cover-footer">
            <span>
              {book.finalized_chapters}/{book.planned_chapters ?? "?"} 章
            </span>
            <span>{pct}%</span>
          </span>
          <span className="shelf-book-progress" aria-hidden="true">
            <span style={{ width: `${pct}%` }} />
          </span>
          <span className="shelf-book-open-hint">
            <ArrowUpRight size={12} /> 打开作品
          </span>
        </span>
      </button>

      <div className="shelf-book-label">
        <span className={clsx("shelf-status", statusTone(book.lifecycle_status))}>
          <span className="shelf-status-dot" />
          {LIFECYCLE_LABEL[book.lifecycle_status] || book.lifecycle_status}
        </span>
        <span className="shelf-book-task" title={book.active_task?.label || "暂无活动任务"}>
          {book.active_task?.label || "点击翻开作品"}
        </span>
        <BookOpen size={12} className="shelf-book-label-icon" aria-hidden="true" />
      </div>

      <div className="shelf-book-actions">
        {book.unresolved_risk_count > 0 && (
          <span className="shelf-risk" title={`${book.unresolved_risk_count} 项待处理风险`}>
            <AlertTriangle size={11} /> {book.unresolved_risk_count}
          </span>
        )}
        <button
          type="button"
          title={`删除《${book.title}》`}
          aria-label={`删除《${book.title}》`}
          className="shelf-delete"
          onClick={(event) => {
            event.stopPropagation();
            onDelete(book);
          }}
        >
          <Trash2 size={11} />
        </button>
      </div>
    </article>
  );
}