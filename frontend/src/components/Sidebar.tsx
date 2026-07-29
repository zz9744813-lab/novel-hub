import {
  BookOpen,
  GitGraph,
  FileText,
  Brain,
  AlertTriangle,
  Plus,
  PenTool,
  Cpu,
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
} from "lucide-react";
import clsx from "clsx";

interface Props {
  tab: string;
  setTab: (t: any) => void;
  onNewBook: () => void;
  onBackToBooks?: () => void;
  selectedBookId: string | null;
  selectedBookTitle?: string | null;
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
  { id: "memory", label: "记忆", icon: Brain, desc: "MEM", needsBook: true },
  { id: "prompts", label: "提示词工坊", icon: Sparkles, desc: "PROMPT", needsBook: false },
  { id: "genre", label: "文风档案", icon: Palette, desc: "GENRE", needsBook: false },
  { id: "research", label: "调研", icon: Globe, desc: "RES", needsBook: false },
  { id: "diagnostics", label: "高级诊断", icon: Wrench, desc: "DIAG", needsBook: true },
];

const diagTabs = [
  { id: "context", label: "Context 检视", icon: Package, desc: "CTX", needsBook: false },
  { id: "models", label: "模型绑定", icon: Cpu, desc: "MODEL", needsBook: false },
  { id: "audit", label: "漂移审计", icon: AlertTriangle, desc: "DRIFT", needsBook: true },
];

export function Sidebar({
  tab,
  setTab,
  onNewBook,
  onBackToBooks,
  selectedBookId,
  selectedBookTitle,
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
          "w-full flex items-center gap-2.5 px-3 py-[7px] rounded text-xs text-left transition-all duration-150",
          active
            ? "bg-brand-muted text-brand-accent"
            : disabled
            ? "text-text-disabled cursor-not-allowed"
            : "text-text-tertiary hover:bg-bg-hover hover:text-text-secondary"
        )}
      >
        <Icon size={14} className={active ? "text-brand-accent" : "text-text-disabled"} />
        <div className="flex-1 min-w-0">
          <div style={{ fontWeight: active ? 510 : 400 }}>{t.label}</div>
        </div>
        <span className="text-2xs font-mono text-text-disabled">{t.desc}</span>
      </button>
    );
  };

  return (
    <aside className="w-56 bg-bg-panel border-r border-border flex flex-col shrink-0">
      <div className="h-11 flex items-center px-4 border-b border-border">
        <BookOpen size={15} className="text-brand" />
        <span className="ml-2 text-xs text-text-primary tracking-wide" style={{ fontWeight: 510 }}>
          NovelForge
        </span>
        <span className="ml-auto text-2xs text-text-disabled font-mono">v8.0</span>
      </div>

      {selectedBookId && (
        <div className="px-2 pt-2 pb-1 border-b border-border/60">
          <button
            type="button"
            onClick={() => onBackToBooks?.()}
            className="w-full flex items-center gap-2 px-2.5 py-2 rounded-md text-xs text-left
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

      <nav className="flex-1 p-2 space-y-px overflow-auto">
        <div className="px-3 py-1 text-2xs text-text-disabled">全局</div>
        {globalTabs.map((t) =>
          renderBtn(
            t,
            t.id === "settings" &&
              ["settings", "models", "context", "genre", "research"].includes(tab) &&
              !["home", "outline", "chapters", "writing", "memory", "prompts", "diagnostics", "audit"].includes(tab)
                ? tab === "settings" ||
                  ["models", "context", "genre", "research"].includes(tab)
                : undefined
          )
        )}

        {inBook && (
          <>
            <div className="mt-2 pt-2 border-t border-border/50 px-3 py-1 text-2xs text-text-disabled">
              当前作品
            </div>
            {bookTabs.map((t) => renderBtn(t))}
          </>
        )}

        {showDiag && (
          <div className="mt-2 pt-2 border-t border-border/50 space-y-px">
            <div className="px-3 py-1 text-2xs text-text-disabled">工程诊断</div>
            {diagTabs.map((t) => {
              const Icon = t.icon;
              const active = tab === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={clsx(
                    "w-full flex items-center gap-2.5 px-3 py-[7px] rounded text-xs text-left",
                    active ? "bg-brand-muted text-brand-accent" : "text-text-tertiary hover:bg-bg-hover"
                  )}
                >
                  <Icon size={13} />
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
          className="btn-primary w-full flex items-center justify-center gap-1.5 py-[7px] text-xs rounded-md"
        >
          <Plus size={13} />
          新建小说
        </button>
      </div>
    </aside>
  );
}
