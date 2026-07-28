import clsx from "clsx";
import { AlertTriangle, BookOpen } from "lucide-react";
import { BookshelfBook, LIFECYCLE_LABEL } from "./library.types";

function progressPct(b: BookshelfBook): number {
  const planned = b.planned_chapters || 0;
  if (!planned) return b.finalized_chapters > 0 ? 5 : 0;
  return Math.min(100, Math.round((b.finalized_chapters / planned) * 100));
}

export function BookCard({
  book,
  onOpen,
}: {
  book: BookshelfBook;
  onOpen: (id: string) => void;
}) {
  const pct = progressPct(book);
  const style = book.cover_style || {
    background: "linear-gradient(135deg,#1a1a2e,#0f3460)",
    accent: "#e94560",
    bg: "#1a1a2e",
  };

  return (
    <button
      type="button"
      onClick={() => onOpen(book.book_id)}
      className="group text-left rounded-xl border border-border bg-bg-panel hover:border-brand/50 hover:bg-bg-hover/40 transition-all overflow-hidden flex flex-col shadow-sm"
    >
      <div
        className="relative h-40 w-full flex items-end p-3"
        style={{ background: style.background }}
      >
        <div className="absolute inset-0 opacity-20 bg-[radial-gradient(circle_at_30%_20%,white,transparent_55%)]" />
        <div className="relative">
          <div className="text-[10px] uppercase tracking-widest text-white/70 mb-1">NovelForge</div>
          <div className="text-sm text-white font-medium line-clamp-2 drop-shadow" style={{ fontWeight: 510 }}>
            {book.title}
          </div>
        </div>
        {book.unresolved_risk_count > 0 && (
          <span className="absolute top-2 right-2 flex items-center gap-1 text-2xs bg-black/50 text-amber-300 px-1.5 py-0.5 rounded">
            <AlertTriangle size={10} />
            {book.unresolved_risk_count}
          </span>
        )}
      </div>

      <div className="p-3 flex-1 flex flex-col gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={clsx(
              "badge text-2xs",
              book.lifecycle_status === "needs_human" && "bg-amber-500/15 text-amber-300",
              book.lifecycle_status === "writing" && "bg-brand/15 text-brand-accent",
              book.lifecycle_status === "completed" && "bg-success/15 text-success",
              !["needs_human", "writing", "completed"].includes(book.lifecycle_status) &&
                "bg-bg-canvas text-text-tertiary"
            )}
          >
            {LIFECYCLE_LABEL[book.lifecycle_status] || book.lifecycle_status}
          </span>
          {(book.tags || []).slice(0, 3).map((t) => (
            <span key={t} className="text-2xs text-text-disabled border border-border rounded px-1.5 py-0.5">
              {t}
            </span>
          ))}
        </div>

        {book.logline && (
          <p className="text-2xs text-text-tertiary line-clamp-2">{book.logline}</p>
        )}

        <div className="mt-auto space-y-1.5">
          <div className="flex justify-between text-2xs text-text-disabled font-mono">
            <span>
              {book.finalized_chapters}/{book.planned_chapters ?? "?"} 章
            </span>
            <span>{(book.finalized_words || 0).toLocaleString()} 字</span>
          </div>
          <div className="h-1.5 rounded-full bg-bg-canvas overflow-hidden">
            <div
              className="h-full rounded-full bg-brand-accent/80 transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          {book.active_task ? (
            <div className="text-2xs text-brand-accent truncate">{book.active_task.label}</div>
          ) : (
            <div className="text-2xs text-text-disabled flex items-center gap-1">
              <BookOpen size={10} />
              {book.updated_at ? new Date(book.updated_at).toLocaleString() : "—"}
            </div>
          )}
        </div>
      </div>
    </button>
  );
}
