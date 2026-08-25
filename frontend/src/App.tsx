import { useEffect, useState, useCallback } from "react";
import { useStore } from "./store";
import { Sidebar } from "./components/Sidebar";
import { OutlinePage } from "./components/OutlinePage";
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
import { WritingDeskPage } from "./features/writing-session/WritingDeskPage";
import { ResearchPage } from "./features/research/ResearchPage";
import { EditorialPage } from "./features/editorial/EditorialPage";
import {
  clearAdminToken,
  getAdminToken,
  getEmbeddedToken,
  getTokenDraft,
  isRemembered,
  setAdminToken,
  verifyAdminTokenStatus,
  api,
} from "./api";
import { ArrowLeft, Download, Eye, EyeOff, KeyRound, Loader2, LogOut, Moon, Plus, ShieldCheck, Sun } from "lucide-react";
import { applyTheme, getStoredTheme, initSystemThemeListener, type ThemeMode } from "./theme";

type Tab =
  | "library"
  | "tasks"
  | "references"
  | "settings"
  | "home"
  | "outline"
  | "chapters"
  | "writing"
  | "editorial"
  | "memory"
  | "prompts"
  | "audit"
  | "context"
  | "models"
  | "model-center"
  | "genre"
  | "research"
  | "diagnostics";

export default function App() {
  const { fetchBooks, books, selectedBookId, selectBook, error: storeError } = useStore();
  const [tab, setTab] = useState<Tab>("library");
  const [showCreate, setShowCreate] = useState(false);
  const [tabKey, setTabKey] = useState(0);
  const [authed, setAuthed] = useState(false);
  const [booting, setBooting] = useState(true);
  const [tokenInput, setTokenInput] = useState<string>(() => getTokenDraft());
  const [remember, setRemember] = useState<boolean>(() => isRemembered());
  const [showToken, setShowToken] = useState(false);
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [autoFilled, setAutoFilled] = useState<boolean>(() => !!getTokenDraft());
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<string | null>(null);
  const [theme, setTheme] = useState<ThemeMode>(() => getStoredTheme());
  const [appVersion, setAppVersion] = useState("");

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // v9.5: follow OS theme changes while mode === "system" (spec §91)
  useEffect(() => {
    const stop = initSystemThemeListener();
    return stop;
  }, []);

  // Dynamic version label (spec v9.2 §19) — replaces the hardcoded "v8.0".
  useEffect(() => {
    if (!authed) return;
    api.system
      .version()
      .then((v) => setAppVersion(v.app_version))
      .catch(() => {});
  }, [authed]);

  // Silent session restore: verify a saved token before showing any UI.
  // "unreachable" keeps the token and enters optimistically (old behavior) —
  // a 401 later will still kick back to the login screen.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = getAdminToken();
      if (stored) {
        const result = await verifyAdminTokenStatus(stored);
        if (cancelled) return;
        if (result === "invalid") {
          clearAdminToken();
        } else {
          setAuthed(true);
        }
      }
      if (!cancelled) setBooting(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

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
    const onUnauth = () => {
      setAuthed(false);
      setTokenInput(getTokenDraft());
      setAutoFilled(!!getTokenDraft());
    };
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

  const handleLogin = async (tokenOverride?: string) => {
    setAuthBusy(true);
    setAuthError("");
    const token = (tokenOverride ?? tokenInput).trim();
    if (!token) {
      setAuthError("请输入 Admin Token");
      setAuthBusy(false);
      return;
    }
    const result = await verifyAdminTokenStatus(token);
    setAuthBusy(false);
    if (result === "invalid") {
      setAuthError("Token 无效，请检查后重试");
      clearAdminToken();
      return;
    }
    if (result === "unreachable") {
      setAuthError("无法连接服务，请确认后端已启动后重试");
      return;
    }
    setAdminToken(token, remember);
    setAuthed(true);
    setTokenInput("");
    setAutoFilled(false);
  };

  // Build-time token: auto-submit once when no saved session exists.
  useEffect(() => {
    if (booting || authed) return;
    const embedded = getEmbeddedToken();
    if (embedded && !getAdminToken() && tokenInput === embedded) {
      handleLogin(embedded);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booting]);

  const handleLogout = () => {
    clearAdminToken();
    setAuthed(false);
  };

  if (booting) {
    return (
      <div className="auth-shell min-h-screen flex items-center justify-center bg-bg-canvas">
        <FluidBackground />
        <div className="auth-boot relative z-10 flex flex-col items-center gap-4">
          <div className="auth-logo-mark auth-logo-pulse">
            <svg viewBox="0 0 32 32" width="30" height="30" fill="none" aria-hidden="true">
              <path d="M8 22V10h4l6 8V10h3v12h-4l-6-8v8z" fill="currentColor" />
            </svg>
          </div>
          <p className="text-caption text-text-tertiary">正在恢复上次会话…</p>
        </div>
      </div>
    );
  }

  if (!authed) {
    return (
      <div className="auth-shell min-h-screen flex items-center justify-center bg-bg-canvas p-6">
        <FluidBackground />
        <div className="relative z-10 w-full max-w-[400px] animate-slide-up">
          <div className="auth-card panel-elevated rounded-card p-8">
            <div className="auth-card-header">
              <div className="auth-logo-mark">
                <svg viewBox="0 0 32 32" width="26" height="26" fill="none" aria-hidden="true">
                  <path d="M8 22V10h4l6 8V10h3v12h-4l-6-8v8z" fill="currentColor" />
                </svg>
              </div>
              <div className="min-w-0">
                <h1 className="text-h2 text-text-primary" style={{ fontWeight: 590 }}>
                  NovelForge
                </h1>
                <p className="text-caption text-text-tertiary mt-0.5">小说锻造工坊 · 输入访问令牌进入</p>
              </div>
            </div>

            <div className="auth-field">
              <label htmlFor="admin-token" className="auth-label">
                <KeyRound size={12} />
                访问令牌
                <span className="auth-label-hint">ADMIN_API_TOKEN</span>
              </label>
              <div className="auth-input-wrap">
                <input
                  id="admin-token"
                  type={showToken ? "text" : "password"}
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  className="auth-input"
                  placeholder="粘贴你的 Admin Token"
                  value={tokenInput}
                  onChange={(e) => {
                    setTokenInput(e.target.value);
                    if (authError) setAuthError("");
                    if (autoFilled) setAutoFilled(false);
                  }}
                  onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                  autoFocus
                />
                <button
                  type="button"
                  className="auth-input-toggle"
                  onClick={() => setShowToken((v) => !v)}
                  aria-label={showToken ? "隐藏令牌" : "显示令牌"}
                  title={showToken ? "隐藏令牌" : "显示令牌"}
                >
                  {showToken ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
              {authError ? (
                <p className="auth-error">{authError}</p>
              ) : autoFilled ? (
                <p className="auth-hint">
                  <ShieldCheck size={12} /> 已自动填入上次使用的令牌，直接进入即可
                </p>
              ) : (
                <p className="auth-hint">令牌来自服务端 ADMIN_API_TOKEN 环境变量</p>
              )}
            </div>

            <label className="auth-remember" htmlFor="auth-remember">
              <input
                id="auth-remember"
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              <span className="auth-checkbox" aria-hidden="true">
                <svg viewBox="0 0 12 12" width="10" height="10" fill="none">
                  <path d="M2 6.2 4.8 9 10 3.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </span>
              <span className="text-caption text-text-secondary">在这台设备上记住令牌（下次免输入）</span>
            </label>

            <button
              onClick={() => handleLogin()}
              disabled={authBusy}
              className="btn-primary w-full px-4 py-2.5 text-body rounded-control"
            >
              {authBusy ? (
                <>
                  <Loader2 size={14} className="animate-spin" /> 验证中…
                </>
              ) : (
                "进入书架"
              )}
            </button>
          </div>

          <p className="auth-footer text-caption">
            NovelForge v9.2 · 令牌仅保存在本机浏览器，不会上传
          </p>
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
    if (tab === "research") {
      return <ResearchPage bookId={selectedBookId ?? undefined} />;
    }
    if (tab === "editorial") {
      return <EditorialPage bookId={selectedBookId ?? undefined} />;
    }
    if (tab === "settings") {
      return <SystemSettingsPage initialTab="models" />;
    }
    if (tab === "prompts") return <PromptStudioPage />;
    if (tab === "models") return <SystemSettingsPage initialTab="models" />;
    if (tab === "model-center") return <SystemSettingsPage initialTab="models" />;  // redirect (v9.6 §73)
    // System-level tabs: always mount (no book gate) — pick book inside panel if needed
    if (tab === "context") return <SystemSettingsPage initialTab="context" />;
    if (tab === "genre") return <SystemSettingsPage initialTab="genre" />;
    // Research page handled above (no book gate)

    if (!selectedBookId) {
      return (
        <LibraryPage
          onOpenBook={handleOpenBook}
          onNewBook={() => setShowCreate(true)}
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
            onOpenWritingDesk={() => {
              setTab("writing");
              setTabKey((k) => k + 1);
            }}
          />
        );
      case "outline":
        return <OutlinePage bookId={selectedBookId} onNavigate={handleTabChange} />;
      case "chapters":
        return <ChapterList bookId={selectedBookId} />;
      case "writing":
        return (
          <WritingDeskPage
            bookId={selectedBookId}
            onStartWriting={() => {
              setTab("home");
              setTabKey((k) => k + 1);
            }}
            onOpenEditorial={() => {
              setTab("editorial");
              setTabKey((k) => k + 1);
            }}
            onOpenModelSetup={() => {
              setTab("settings");
              setTabKey((k) => k + 1);
            }}
          />
        );
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
        appVersion={appVersion}
      />

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <header className="app-header flex items-center justify-between px-5 h-12 border-b border-border shrink-0 gap-3">
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
              <span className="inline-block w-1 h-3.5 rounded-sm bg-brand-accent shadow-glow-accent" aria-hidden="true" />
              NovelForge
            </span>
            <span className="text-text-disabled font-mono text-2xs shrink-0 px-1.5 py-0.5 rounded bg-bg-surface/60 border border-border-subtle">{appVersion || "v9.2"}</span>
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
              title={theme === "dark" ? "切换到日间模式" : theme === "light" ? "切换跟随系统" : "切换到夜间模式"}
              aria-label="切换主题"
              onClick={() =>
                setTheme((current) =>
                  current === "dark" ? "light" : current === "light" ? "system" : "dark"
                )
              }
            >
              {theme === "dark" ? <Sun size={14} /> : theme === "light" ? <Moon size={14} /> : <Sun size={14} />}
              <span>{theme === "dark" ? "日间" : theme === "light" ? "夜间" : "跟随系统"}</span>
            </button>
            <button
              onClick={handleLogout}
              className="btn-ghost p-1.5 rounded"
              title="退出登录"
              aria-label="退出登录"
            >
              <LogOut size={14} />
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
