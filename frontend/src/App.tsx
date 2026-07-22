import { useEffect } from "react";
import { useStore } from "./store";
import { Sidebar } from "./components/Sidebar";
import { BookList } from "./components/BookList";
import { OutlineGraph } from "./components/OutlineGraph";
import { ChapterList } from "./components/ChapterList";
import { MemoryPanel } from "./components/MemoryPanel";
import { DriftAuditPanel } from "./components/DriftAuditPanel";
import { ResourceBar } from "./components/ResourceBar";
import { CreateBookModal } from "./components/CreateBookModal";
import { useState } from "react";

type Tab = "overview" | "outline" | "chapters" | "memory" | "audit";

export default function App() {
  const { fetchBooks, books, selectedBookId } = useStore();
  const [tab, setTab] = useState<Tab>("overview");
  const [showCreate, setShowCreate] = useState(false);

  useEffect(() => { fetchBooks(); }, []);

  const selectedBook = books.find((b) => b.book_id === selectedBookId);

  return (
    <div className="flex h-screen overflow-hidden bg-ink-950">
      {/* 侧栏 */}
      <Sidebar tab={tab} setTab={setTab} onNewBook={() => setShowCreate(true)} />

      {/* 主区域 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* 顶栏 */}
        <header className="flex items-center justify-between px-6 h-14 border-b border-ink-800 glass">
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-semibold text-ink-300">
              <span className="text-gradient text-lg font-bold">NovelForge</span>
              <span className="text-ink-500 text-xs ml-2 font-normal">v7.3 · SQL-first</span>
            </h1>
            {selectedBook && (
              <span className="text-ink-600">/</span>
            )}
            {selectedBook && (
              <span className="text-sm text-ink-400">{selectedBook.title}</span>
            )}
          </div>
          <ResourceBar />
        </header>

        {/* 内容 */}
        <main className="flex-1 overflow-auto p-8 animate-fade-in">
          {!selectedBookId || tab === "overview" ? (
            <BookList onNewBook={() => setShowCreate(true)} />
          ) : tab === "outline" ? (
            <OutlineGraph bookId={selectedBookId} />
          ) : tab === "chapters" ? (
            <ChapterList bookId={selectedBookId} />
          ) : tab === "memory" ? (
            <MemoryPanel bookId={selectedBookId} />
          ) : tab === "audit" ? (
            <DriftAuditPanel bookId={selectedBookId} />
          ) : null}
        </main>
      </div>

      {showCreate && <CreateBookModal onClose={() => setShowCreate(false)} />}
    </div>
  );
}