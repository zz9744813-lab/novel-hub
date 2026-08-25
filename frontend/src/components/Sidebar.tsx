import {
  GitGraph,
  FileText,
  Brain,
  AlertTriangle,
  Plus,
  PenTool,
  Package,
  Palette,
  Globe,
  ArrowLeft,
  Library,
  Home,
  Sparkles,
  Wrench,
  ListTodo,
  FolderOpen,
  Settings,
  ClipboardCheck,
} from "lucide-react";
import clsx from "clsx";

interface Props {
  tab: string;
  setTab: (t: any) => void;
  onNewBook: () => void;
  onBackToBooks?: () => void;
  selectedBookId: string | null;
  selectedBookTitle?: string | null;
  appVersion?: string;
}

/** Global IA (v8): always visible — 书架 / 任务 / 资料 / 设置 */
const globalTabs = [
  { id: "library", label: "我的书架", icon: Library, desc: "BOOKS", needsBook: false },
  { id: "tasks", label: "写作任务", icon: ListTodo, desc: "TASKS", needsBook: false },
  { id: "references", label: "参考资料库", icon: FolderOpen, desc: "REF", needsBook: false },
  { id: "settings", label: "系统设置", icon: Settings, desc: "SYS", needsBook: false },
];

/** Single-book studio nav */
const bookTabs = [
  { id: "home", label: "作品首页", icon: Home, desc: "HOME", needsBook: true },
  { id: "outline", label: "大纲", icon: GitGraph, desc: "OUT", needsBook: true },
  { id: "chapters", label: "章节", icon: FileText, desc: "CH", needsBook: true },
  { id: "writing", label: "写作台", icon: PenTool, desc: "WRITE", needsBook: true },
  { id: "editorial", label: "人工审核", icon: ClipboardCheck, desc: "EDIT", needsBook: false },
  { id: "memory", label: "记忆", icon: Brain, desc: "MEM", needsBook: true },
  { id: "prompts", label: "提示词工坊", icon: Sparkles, desc: "PROMPT", needsBook: false },
  { id: "genre", label: "文风档案", icon: Palette, desc: "GENRE", needsBook: false },
  { id: "research", label: "调研", icon: Globe, desc: "RES", needsBook: false },
  { id: "diagnostics", label: "高级诊断", icon: Wrench, desc: "DIAG", needsBook: true },
];

const diagTabs = [
  { id: "context", label: "Context 检视", icon: Package, desc: "CTX", needsBook: false },
  { id: "audit", label: "漂移审计", icon: AlertTriangle, desc: "DRIFT", needsBook: true },
];

function BrandMark() {
  return (
    <span className="sidebar-brand-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32" width="17" height="17" fill="none">
        <path d="M8 22V10h4l6 8V10h3v12h-4l-6-8v8z" fill="currentColor" />
      </svg>
    </span>
  );
}

export function Sidebar({
  tab,
  setTab,
  onNewBook,
  onBackToBooks,
  selectedBookId,
  selectedBookTitle,
  appVersion,
}: Props) {
  const inBook = !!selectedBookId;
  const showDiag =
    inBook && (tab === "diagnostics" || diagTabs.some((d) => d.id === tab));

  const renderBtn = (t: (typeof globalTabs)[0], activeOverride?: boolean) => {
    const Icon = t.icon;
    const active =
      activeOverride ??
      (tab === t.id ||
        (t.id === "diagnostics" && showDiag && diagTabs.some((d) => d.id === tab)) ||
        (t.id === "settings" &&
          ["settings", "models", "context"].includes(tab) &&
          !inBook));
    const disabled = t.needsBook && !selectedBookId;
    return (
      <button
        key={t.id}
        type="button"
        onClick={() => !disabled && setTab(t.id)}
        title={disabled ? "请先选择一本小说" : undefined}
        className={clsx(
          "sidebar-nav-item group w-full flex items-center gap-2.5 px-2.5 py-2 rounded-[9px] text-xs text-left transition-all duration-150 relative",
          active
            ? "is-active"
            : disabled
            ? "text-text-disabled cursor-not-allowed"
            : "text-text-tertiary hover:bg-bg-hover hover:text-text-secondary"
        )}
      >
        {active && (
          <span
            className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-r-full bg-brand-accent"
            style={{ boxShadow: "0 0 8px rgba(139,142,255,0.5)" }}
            aria-hidden="true"
          />
        )}
        <span className={clsx("sidebar-nav-icon", active && "is-active")}>
          <Icon size={14} />
        </span>
        <div className="flex-1 min-w-0">
          <div style={{ fontWeight: active ? 510 : 400 }}>{t.label}</div>
        </div>
        <span className="text-2xs font-mono text-text-disabled opacity-70 group-hover:opacity-100 transition-opacity">{t.desc}</span>
      </button>
    );
  };

  return (
    <aside className="sidebar-shell w-60 bg-bg-panel border-r border-border flex flex-col shrink-0">
      <div className="h-12 flex items-center gap-2.5 px-3.5 border-b border-border">
        <BrandMark />
        <span className="text-xs text-text-primary tracking-wide" style={{ fontWeight: 590 }}>
          NovelForge
        </span>
        <span className="ml-auto text-2xs text-text-disabled font-mono px-1.5 py-0.5 rounded bg-bg-surface/70 border border-border-subtle">{appVersion || "v9.3"}</span>
      </div>

      {selectedBookId && (
        <div className="px-2 pt-2.5 pb-1.5 border-b border-border/60">
          <button
            type="button"
            onClick={() => onBackToBooks?.()}
            className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-[9px] text-xs text-left
              bg-bg-surface border border-border hover:border-brand/40 hover:bg-brand-muted/40
              text-text-secondary hover:text-brand-accent transition-all"
          >
            <ArrowLeft size={13} className="shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-2xs text-text-disabled">返回书架</div>
              <div className="truncate" style={{ fontWeight: 510 }}>
                {selectedBookTitle || "当前作品"}
              </div>
            </div>
          </button>
        </div>
      )}

      <nav className="flex-1 p-2 space-y-px overflow-auto sidebar-nav">
        <div className="px-3 py-1 text-2xs text-text-disabled tracking-[0.08em]">全局</div>
        {globalTabs.map((t) =>
          renderBtn(
            t,
            t.id === "settings" &&
              ["settings", "context", "genre", "research"].includes(tab) &&
              !["home", "outline", "chapters", "writing", "memory", "prompts", "diagnostics", "audit"].includes(tab)
                ? tab === "settings" ||
                  ["context", "genre", "research"].includes(tab)
                : undefined
          )
        )}

        {inBook && (
          <>
            <div className="mt-3 pt-2.5 border-t border-border/50 px-3 py-1 text-2xs text-text-disabled tracking-[0.08em]">
              当前作品
            </div>
            {bookTabs.map((t) => renderBtn(t))}
          </>
        )}

        {showDiag && (
          <div className="mt-2 pt-2 border-t border-border/50 space-y-px">
            <div className="px-3 py-1 text-2xs text-text-disabled tracking-[0.08em]">工程诊断</div>
            {diagTabs.map((t) => {
              const Icon = t.icon;
              const active = tab === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={clsx(
                    "w-full flex items-center gap-2 px-2.5 py-1.5 rounded text-left transition-colors duration-100",
                    active
                      ? "bg-bg-hover text-text-secondary"
                      : "text-text-disabled hover:bg-bg-hover hover:text-text-tertiary"
                  )}
                  style={{ fontSize: 11 }}
                >
                  <Icon size={12} className={active ? "text-text-tertiary" : "text-text-disabled"} />
                  <span>{t.label}</span>
                </button>
              );
            })}
          </div>
        )}
      </nav>

      <div className="p-2 border-t border-border">
        <button
          type="button"
          onClick={onNewBook}
          className="btn-primary w-full flex items-center justify-center gap-1.5 py-2.5 text-xs rounded-[9px]"
          title="新建小说 (Ctrl/Cmd+N)"
        >
          <Plus size={14} />
          新建小说
          <kbd className="ml-1 text-2xs opacity-50 hidden sm:inline">⌘N</kbd>
        </button>
      </div>
    </aside>
  );
}
