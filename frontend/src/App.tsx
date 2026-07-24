import { useEffect, useState, useCallback } from "react";
import { useStore } from "./store";
import { Sidebar } from "./components/Sidebar";
import { BookList } from "./components/BookList";
import { OutlineGraph } from "./components/OutlineGraph";
import { ChapterList } from "./components/ChapterList";
import { MemoryPanel } from "./components/MemoryPanel";
import { DriftAuditPanel } from "./components/DriftAuditPanel";
import { ResourceBar } from "./components/ResourceBar";
import { CreateBookModal } from "./components/CreateBookModal";

type Tab = "overview" | "outline" | "chapters" | "memory" | "audit";

export default function App() {
  const { fetchBooks, books, selectedBookId } = useStore();
  const [tab, setTab] = useState<Tab>("overview");
  const [showCreate, setShowCreate] = useState(false);
  const [tabKey, setTabKey] = useState(0);

  useEffect(() => { fetchBooks(); }, []);

  const selectedBook = books.find((b) => b.book_id === selectedBookId);

  const handleTabChange = useCallback((newTab: string) => {
    if (newTab !== tab) {
      setTab(newTab as Tab);
      setTabKey(k => k + 1);
    }
  }, [tab]);

  return (
    <div className="flex h-screen overflow-hidden bg-bg-canvas">
      <Sidebar tab={tab} setTab={handleTabChange} onNewBook={() => setShowCreate(true)} selectedBookId={selectedBookId} />

      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center justify-between px-5 h-11 border-b border-border shrink-0 bg-bg-panel">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-semibold text-text-primary tracking-wide" style={{ fontWeight: 510 }}>NovelForge</span>
            <span className="text-text-disabled font-mono text-2xs">v7.3</span>
            {selectedBook && (
              <>
                <span className="text-text-disabled mx-0.5">/</span>
                <span className="text-text-secondary" style={{ fontWeight: 510 }}>{selectedBook.title}</span>
              </>
            )}
          </div>
          <ResourceBar />
        </header>

        {/* Content with page transition */}
        <main key={tabKey} className="flex-1 overflow-auto p-6 animate-page-in">
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
