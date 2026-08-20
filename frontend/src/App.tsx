import { useEffect, useState, useCallback } from "react";
import { useStore } from "./store";
import { Sidebar } from "./components/Sidebar";
import { OutlineGraph } from "./components/OutlineGraph";
import { ChapterList } from "./components/ChapterList";
import { MemoryPanel } from "./components/MemoryPanel";
import { DriftAuditPanel } from "./components/DriftAuditPanel";
import { ResourceBar } from "./components/ResourceBar";
import { ContextInspector } from "./components/ContextInspector";
import { ModelBindingPanel } from "./components/ModelBindingPanel";
import { GenreProfilePanel } from "./components/GenreProfilePanel";
import { ResearchPanel } from "./components/ResearchPanel";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { FluidBackground } from "./components/FluidBackground";
import { LibraryPage } from "./features/library/LibraryPage";
import { BookHomePage } from "./features/book/BookHomePage";
import { ImportWizard } from "./features/import/ImportWizard";
import { PromptStudioPage } from "./features/prompt-studio/PromptStudioPage";
import { WritingTasksPage } from "./features/tasks/WritingTasksPage";
import { ReferencesLibraryPage } from "./features/references/ReferencesLibraryPage";
import { SystemSettingsPage } from "./features/settings/SystemSettingsPage";
import {
  clearAdminToken,
  getAdminToken,
  setAdminToken,
  verifyAdminToken,
  api,
} from "./api";
import { ArrowLeft, Download, Loader2, Moon, Sun, Plus } from "lucide-react";
import { applyTheme, getStoredTheme, type ThemeMode } from "./theme";

type Tab =
  | "library"
  | "tasks"
  | "references"
  | "settings"
  | "home"
  | "outline"
  | "chapters"
  | "writing"
  | "memory"
  | "prompts"
  | "audit"
  | "context"
  | "models"
  | "genre"
  | "research"
  | "diagnostics";

export default function App() {
  const { fetchBooks, books, selectedBookId, selectBook, error: storeError } = useStore();
  const [tab, setTab] = useState<Tab>("library");
  const [showCreate, setShowCreate] = useState(false);
  const [tabKey, setTabKey] = useState(0);
  const [authed, setAuthed] = useState<boolean>(() => !!getAdminToken());
  const [tokenInput, setTokenInput] = useState("");
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [theme, setTheme] = useState<ThemeMode>(() => getStoredTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (!authed) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "n") {
        e.preventDefault();
        setShowCreate(true);
      }
      if (e.key === "Escape") {
        if (showCreate) setShowCreate(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [authed, showCreate]);

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

  const handleOpenBook = useCallback(
    (bookId: string) => {
      selectBook(bookId);
      setTab("home");
      setTabKey((k) => k + 1);
    },
    [selectBook]
  );

  const handleBackToBooks = useCallback(() => {
    useStore.setState({ selectedBookId: null });
    setTab("library");
    setTabKey((k) => k + 1);
  }, []);

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
      <div className="login-shell min-h-screen flex items-center justify-center bg-bg-canvas p-6">
        <FluidBackground />
        <div className="panel-elevated rounded-card p-8 w-full max-w-sm space-y-4 relative z-10">
          <div>
            <h1 className="text-h2 text-text-primary" style={{ fontWeight: 590 }}>
              NovelForge 登录
            </h1>
            <p className="text-body text-text-tertiary mt-1">
              输入 ADMIN_API_TOKEN（仅保存在当前标签页 sessionStorage）
            </p>
          </div>
          <input
            type="password"
            className="w-full rounded-control border border-border bg-bg-panel px-3 py-2 text-body text-text-primary"
            placeholder="Bearer Token"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLogin()}
          />
          {authError && <p className="text-caption text-red-400">{authError}</p>}
          <button
            onClick={handleLogin}
            disabled={authBusy}
            className="btn-primary w-full px-4 py-2 text-body rounded-control"
          >
            {authBusy ? "验证中…" : "进入书架"}
          </button>
        </div>
      </div>
    );
  }

  const renderMain = () => {
    if (tab === "library" || tab === "overview" as any) {
      return (
        <LibraryPage
          onOpenBook={handleOpenBook}
          onNewBook={() => setShowCreate(true)}
        />
      );
    }
    if (tab === "tasks") return <WritingTasksPage />;
    if (tab === "references") {
      return (
        <ReferencesLibraryPage
          onOpenGenre={() => setTab("genre")}
          onOpenResearch={() => setTab("research")}
        />
      );
    }
    if (tab === "settings") {
      return <SystemSettingsPage initialTab="models" />;
    }
    if (tab === "prompts") return <PromptStudioPage />;
    if (tab === "models") return <SystemSettingsPage initialTab="models" />;
    // System-level tabs: always mount (no book gate) — pick book inside panel if needed
    if (tab === "context") return <SystemSettingsPage initialTab="context" />;
    if (tab === "genre") return <SystemSettingsPage initialTab="genre" />;
    if (tab === "research") return <SystemSettingsPage initialTab="research" />;
    if (!selectedBookId) {
      return (
        <EmptyBookHint
          title="尚未选择小说"
          tip="请先在「我的书架」选择或新建"
          onNew={() => setShowCreate(true)}
        />
      );
    }
    switch (tab) {
      case "home":
        return (
          <BookHomePage
            bookId={selectedBookId}
            onContinueWrite={() => {
              setTab("chapters");
              setTabKey((k) => k + 1);
            }}
            onOpenChapters={() => {
              setTab("chapters");
              setTabKey((k) => k + 1);
            }}
          />
        );
      case "outline":
        return <OutlineGraph bookId={selectedBookId} />;
      case "chapters":
      case "writing":
        return <ChapterList bookId={selectedBookId} />;
      case "memory":
        return <MemoryPanel bookId={selectedBookId} />;
      case "audit":
        return <DriftAuditPanel bookId={selectedBookId} />;
      case "diagnostics":
        return (
          <div className="space-y-4 p-1">
            <p className="text-xs text-text-tertiary">
              高级诊断：Context / 模型路由 / 漂移。左侧展开「工程诊断」子项。
            </p>
            <ContextInspector bookId={selectedBookId} />
          </div>
        );
      default:
        return (
          <LibraryPage
            onOpenBook={handleOpenBook}
            onNewBook={() => setShowCreate(true)}
          />
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
        <header className="flex items-center justify-between px-5 h-11 border-b border-border shrink-0 bg-bg-panel/80 backdrop-blur-sm gap-3">
          <div className="flex items-center gap-2.5 text-xs min-w-0">
            {selectedBookId && tab !== "library" && (
              <button
                onClick={handleBackToBooks}
                className="btn-ghost p-1.5 rounded shrink-0"
                title="返回书架"
              >
                <ArrowLeft size={14} />
              </button>
            )}
            <span
              className="font-semibold text-text-primary tracking-wide shrink-0 flex items-center gap-1.5"
              style={{ fontWeight: 510 }}
            >
              <span className="inline-block w-1 h-3.5 rounded-sm bg-brand-accent" aria-hidden="true" />
              NovelForge
            </span>
            <span className="text-text-disabled font-mono text-2xs shrink-0 px-1.5 py-0.5 rounded bg-bg-surface/60 border border-border-subtle">v8.0</span>
            {selectedBook && (
              <>
                <span className="text-text-disabled/60 shrink-0">/</span>
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
              type="button"
              className="theme-toggle"
              title={theme === "dark" ? "切换到日间模式" : "切换到夜间模式"}
              aria-label={theme === "dark" ? "切换到日间模式" : "切换到夜间模式"}
              onClick={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
            >
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
              <span>{theme === "dark" ? "日间" : "夜间"}</span>
            </button>
            <button
              onClick={handleLogout}
              className="text-2xs text-text-tertiary hover:text-text-primary px-1"
            >
              退出
            </button>
          </div>
        </header>

        <main key={tabKey} className="flex-1 overflow-auto animate-page-in bg-bg-canvas">
          <ErrorBoundary label={tab} key={`eb-${tabKey}`}>
            {renderMain()}
          </ErrorBoundary>
        </main>
      </div>

      {showCreate && (
        <ImportWizard
          onClose={() => setShowCreate(false)}
          onCommitted={(bookId) => {
            setShowCreate(false);
            fetchBooks();
            handleOpenBook(bookId);
          }}
        />
      )}
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
        新建小说
      </button>
    </div>
  );
}

function Placeholder({ title, tip }: { title: string; tip: string }) {
  return (
    <div className="p-8 max-w-lg">
      <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
        {title}
      </h2>
      <p className="text-xs text-text-tertiary mt-2">{tip}</p>
    </div>
  );
}
