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
import { ContextInspector } from "./components/ContextInspector";
import { ModelBindingPanel } from "./components/ModelBindingPanel";
import { GenreProfilePanel } from "./components/GenreProfilePanel";
import { ResearchPanel } from "./components/ResearchPanel";

type Tab = "overview" | "outline" | "chapters" | "memory" | "audit" | "context" | "models" | "genre" | "research";

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

  const renderMain = () => {
    // Global / no-book tabs
    if (tab === "models") return <ModelBindingPanel />;
    if (tab === "context") {
      return <ContextInspector bookId={selectedBookId || ""} />;
    }
    if (tab === "genre") {
      return selectedBookId
        ? <GenreProfilePanel bookId={selectedBookId} />
        : <EmptyBookHint title="Genre Profile" tip="请先新建/选择一个项目，再管理文风档案" onNew={() => setShowCreate(true)} />;
    }
    if (tab === "research") {
      return selectedBookId
        ? <ResearchPanel bookId={selectedBookId} />
        : <EmptyBookHint title="Research Sessions" tip="请先新建/选择一个项目" onNew={() => setShowCreate(true)} />;
    }
    if (tab === "overview" || !selectedBookId) {
      return <BookList onNewBook={() => setShowCreate(true)} />;
    }
    switch (tab) {
      case "outline": return <OutlineGraph bookId={selectedBookId} />;
      case "chapters": return <ChapterList bookId={selectedBookId} />;
      case "memory": return <MemoryPanel bookId={selectedBookId} />;
      case "audit": return <DriftAuditPanel bookId={selectedBookId} />;
      default: return <BookList onNewBook={() => setShowCreate(true)} />;
    }
  };

  return (
    <div className="flex h-screen overflow-hidden bg-bg-canvas">
      <Sidebar tab={tab} setTab={handleTabChange} onNewBook={() => setShowCreate(true)} selectedBookId={selectedBookId} />

      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="flex items-center justify-between px-5 h-11 border-b border-border shrink-0 bg-bg-panel">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-semibold text-text-primary tracking-wide" style={{ fontWeight: 510 }}>NovelForge</span>
            <span className="text-text-disabled font-mono text-2xs">v7.4</span>
            {selectedBook && (
              <>
                <span className="text-text-disabled mx-0.5">/</span>
                <span className="text-text-secondary" style={{ fontWeight: 510 }}>{selectedBook.title}</span>
              </>
            )}
          </div>
          <ResourceBar />
        </header>

        <main key={tabKey} className="flex-1 overflow-auto p-6 animate-page-in">
          {renderMain()}
        </main>
      </div>

      {showCreate && <CreateBookModal onClose={() => setShowCreate(false)} />}
    </div>
  );
}

function EmptyBookHint({ title, tip, onNew }: { title: string; tip: string; onNew: () => void }) {
  return (
    <div className="panel-elevated rounded-lg p-8 max-w-md mx-auto mt-16 text-center space-y-3">
      <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>{title}</h2>
      <p className="text-xs text-text-tertiary">{tip}</p>
      <button onClick={onNew} className="btn-primary px-4 py-2 text-xs rounded-md">新建项目</button>
    </div>
  );
}
