import { useStore } from "../store";
import { Book as BookIcon, Plus } from "lucide-react";
import clsx from "clsx";

export function BookList({ onNewBook }: { onNewBook: () => void }) {
  const { books, selectedBookId, selectBook } = useStore();
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">{"\u9879\u76ee\u5217\u8868"}</h2>
        <button onClick={onNewBook}
          className="flex items-center gap-1 px-3 py-1.5 rounded-md bg-ink-600 hover:bg-ink-500 text-sm">
          <Plus size={14} />{"\u65b0\u5efa"}
        </button>
      </div>
      <div className="grid gap-3">
        {books.length === 0 ? (
          <div className="text-center text-gray-500 py-12">
            <BookIcon size={48} className="mx-auto mb-3 opacity-30" />
            <p>{"\u8fd8\u6ca1\u6709\u9879\u76ee\uff0c\u70b9\u51fb\"\u65b0\u5efa\"\u521b\u5efa\u4e00\u4e2a\u3002"}</p>
          </div>
        ) : books.map((b) => (
          <div key={b.book_id} onClick={() => selectBook(b.book_id)}
            className={clsx("p-4 rounded-lg border cursor-pointer transition-colors",
              selectedBookId === b.book_id ? "border-accent bg-ink-700" : "border-ink-600 bg-ink-800 hover:border-ink-500")}>
            <div className="flex items-center justify-between">
              <div>
                <div className="font-medium text-gray-200">{b.title}</div>
                <div className="text-sm text-gray-500 mt-1">
                  {b.finalized_chapters || 0} / {b.target_chapters || "?"} {"\u7ae0"} | {(b.finalized_words || 0).toLocaleString()} {"\u5b57"}
                </div>
              </div>
              <div className="text-xs px-2 py-1 rounded bg-ink-600 text-gray-400">{b.status || "idle"}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
