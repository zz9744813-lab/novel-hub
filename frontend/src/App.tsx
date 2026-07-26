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
import { ErrorBoundary } from "./components/ErrorBoundary";
import {
  clearAdminToken,
  getAdminToken,
  setAdminToken,
  verifyAdminToken,
  api,
} from "./api";
import { ArrowLeft, Download, Loader2 } from "lucide-react";

type Tab =
  | "overview"
  | "outline"
  | "chapters"
  | "memory"
  | "audit"
  | "context"
  | "models"
  | "genre"
  | "research";

export default function App() {
  const { fetchBooks, books, selectedBookId, selectBook, error: storeError } = useStore();
  const [tab, setTab] = useState<Tab>("overview");
  const [showCreate, setShowCreate] = useState(false);
  const [tabKey, setTabKey] = useState(0);
  const [authed, setAuthed] = useState<boolean>(() => !!getAdminToken());
  const [tokenInput, setTokenInput] = useState("");
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  useEffect(() => {
    const onUnauth = () => setAuthed(false);
    window.addEventListener("novelforge:unauthorized", onUnauth);
    return () => window.removeEventListener("novelforge:unauthorized", onUnauth);
  }, []);

  useEffect(() => {
    if (!authed) return;
    fetchBooks();
  }, [authed, fetchBooks]);

  const selectedBook = books.find(
    (b: any) => b.book_id === selectedBookId || b.id === selectedBookId
  );

  const handleTabChange = useCallback((newTab: string) => {
    setTab(newTab as Tab);
    setTabKey((k) => k + 1);
  }, []);

  const handleOpenBook = useCallback((bookId: string) => {
    void bookId;
    setTab("chapters");
    setTabKey((k) => k + 1);
  }, []);

  const handleBackToBooks = useCallback(() => {
    selectBook("");
    // store uses string — empty means clear
    useStore.setState({ selectedBookId: null });
    setTab("overview");
    setTabKey((k) => k + 1);
  }, [selectBook]);

  const handleExport = async () => {
    if (!selectedBookId) return;
    setExporting(true);
    setExportMsg(null);
    try {
      await api.books.exportDownload(selectedBookId, (selectedBook as any)?.title);
      setExportMsg("已开始下载");
    } catch (e: any) {
      setExportMsg(e?.message || "导出失败");
    } finally {
      setExporting(false);
      setTimeout(() => setExportMsg(null), 4000);
    }
  };

  const handleLogin = async () => {
    setAuthBusy(true);
    setAuthError("");
    const token = tokenInput.trim();
    if (!token) {
      setAuthError("请输入 Admin Token");
      setAuthBusy(false);
      return;
    }
    const ok = await verifyAdminToken(token);
    setAuthBusy(false);
    if (!ok) {
      setAuthError("Token 无效或服务未就绪");
      clearAdminToken();
      return;
    }
    setAdminToken(token);
    setAuthed(true);
    setTokenInput("");
  };

  const handleLogout = () => {
    clearAdminToken();
    setAuthed(false);
  };

  if (!authed) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg-canvas p-6">
        <div className="panel-elevated rounded-lg p-8 w-full max-w-sm space-y-4">
          <div>
            <h1 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
              NovelForge 登录
            </h1>
            <p className="text-xs text-text-tertiary mt-1">
              输入 ADMIN_API_TOKEN（仅保存在当前标签页 sessionStorage）
            </p>
          </div>
          <input
            type="password"
            className="w-full rounded-md border border-border bg-bg-panel px-3 py-2 text-xs text-text-primary"
            placeholder="Bearer Token"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
          />
          {authError && <p className="text-xs text-red-400">{authError}</p>}
          <button
            onClick={handleLogin}
            disabled={authBusy}
            className="btn-primary w-full px-4 py-2 text-xs rounded-md"
          >
            {authBusy ? "验证中…" : "进入 Cockpit"}
          </button>
        </div>
      </div>
    );
  }

  const renderMain = () => {
    if (tab === "models") return <ModelBindingPanel />;
    if (tab === "context") {
      return <ContextInspector bookId={selectedBookId || ""} />;
    }
    if (tab === "genre") {
      return selectedBookId ? (
        <GenreProfilePanel bookId={selectedBookId} />
      ) : (
        <EmptyBookHint
          title="Genre Profile"
          tip="请先新建/选择一个项目，再管理文风档案"
          onNew={() => setShowCreate(true)}
        />
      );
    }
    if (tab === "research") {
      return selectedBookId ? (
        <ResearchPanel bookId={selectedBookId} />
      ) : (
        <EmptyBookHint
          title="Research Sessions"
          tip="请先新建/选择一个项目"
          onNew={() => setShowCreate(true)}
        />
      );
    }
    if (tab === "overview") {
      return (
        <BookList onNewBook={() => setShowCreate(true)} onOpenBook={handleOpenBook} />
      );
    }
    if (!selectedBookId) {
      return (
        <EmptyBookHint
          title="尚未选择项目"
          tip="请先在「项目总览」选择或新建一个项目"
          onNew={() => setShowCreate(true)}
        />
      );
    }
    switch (tab) {
      case "outline":
        return <OutlineGraph bookId={selectedBookId} />;
      case "chapters":
        return <ChapterList bookId={selectedBookId} />;
      case "memory":
        return <MemoryPanel bookId={selectedBookId} />;
      case "audit":
        return <DriftAuditPanel bookId={selectedBookId} />;
      default:
        return (
          <BookList onNewBook={() => setShowCreate(true)} onOpenBook={handleOpenBook} />
        );
    }
  };

  return (
    <div className="flex h-screen overflow-hidden bg-bg-canvas">
      <Sidebar
        tab={tab}
        setTab={handleTabChange}
        onNewBook={() => setShowCreate(true)}
        onBackToBooks={handleBackToBooks}
        selectedBookId={selectedBookId}
        selectedBookTitle={(selectedBook as any)?.title}
      />

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <header className="flex items-center justify-between px-5 h-12 border-b border-border shrink-0 bg-bg-panel gap-3">
          <div className="flex items-center gap-2 text-xs min-w-0">
            {selectedBookId && tab !== "overview" && (
              <button
                onClick={handleBackToBooks}
                className="btn-ghost p-1.5 rounded shrink-0"
                title="返回项目总览"
              >
                <ArrowLeft size={14} />
              </button>
            )}
            <span
              className="font-semibold text-text-primary tracking-wide shrink-0"
              style={{ fontWeight: 510 }}
            >
              NovelForge
            </span>
            <span className="text-text-disabled font-mono text-2xs shrink-0">v7.4</span>
            {selectedBook && (
              <>
                <span className="text-text-disabled mx-0.5">/</span>
                <span className="text-text-secondary truncate" style={{ fontWeight: 510 }}>
                  {(selectedBook as any).title || "未命名"}
                </span>
              </>
            )}
            {storeError && (
              <span className="text-2xs text-red-400 truncate ml-2" title={storeError}>
                API: {storeError}
              </span>
            )}
            {exportMsg && (
              <span className="text-2xs text-brand-accent truncate ml-2">{exportMsg}</span>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {selectedBookId && (
              <button
                onClick={handleExport}
                disabled={exporting}
                className="btn text-2xs py-1.5 px-2.5"
                title="下载整本小说 .txt"
              >
                {exporting ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Download size={12} />
                )}
                下载小说
              </button>
            )}
            <ResourceBar />
            <button
              onClick={handleLogout}
              className="text-2xs text-text-tertiary hover:text-text-primary px-1"
            >
              退出
            </button>
          </div>
        </header>

        <main key={tabKey} className="flex-1 overflow-auto p-6 animate-page-in bg-bg-canvas">
          <ErrorBoundary label={tab} key={`eb-${tabKey}`}>
            {renderMain()}
          </ErrorBoundary>
        </main>
      </div>

      {showCreate && <CreateBookModal onClose={() => setShowCreate(false)} />}
    </div>
  );
}

function EmptyBookHint({
  title,
  tip,
  onNew,
}: {
  title: string;
  tip: string;
  onNew: () => void;
}) {
  return (
    <div className="panel-elevated rounded-lg p-8 max-w-md mx-auto mt-16 text-center space-y-3">
      <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
        {title}
      </h2>
      <p className="text-xs text-text-tertiary">{tip}</p>
      <button onClick={onNew} className="btn-primary px-4 py-2 text-xs rounded-md">
        新建项目
      </button>
    </div>
  );
}
