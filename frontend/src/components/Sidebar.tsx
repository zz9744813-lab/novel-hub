import { BookOpen, GitGraph, FileText, Brain, AlertTriangle, Plus, PenTool, Cpu, Package, Palette, Globe } from "lucide-react";
import clsx from "clsx";

interface Props {
  tab: string;
  setTab: (t: any) => void;
  onNewBook: () => void;
  selectedBookId: string | null;
}

const tabs = [
  { id: "overview", label: "项目总览", icon: BookOpen, desc: "BOOKS" },
  { id: "outline",  label: "大纲依赖", icon: GitGraph,  desc: "DAG" },
  { id: "chapters", label: "章节流水线", icon: FileText, desc: "PIPE" },
  { id: "memory",   label: "记忆银行", icon: Brain,     desc: "L0-L4" },
  { id: "audit",    label: "漂移审计", icon: AlertTriangle, desc: "DRIFT" },
  { id: "context",  label: "Context", icon: Package, desc: "C-35" },
  { id: "models",   label: "模型绑定", icon: Cpu, desc: "C-21" },
  { id: "genre",    label: "Genre", icon: Palette, desc: "C-27" },
  { id: "research", label: "调研", icon: Globe, desc: "C-32" },
];

export function Sidebar({ tab, setTab, onNewBook, selectedBookId }: Props) {
  return (
    <aside className="w-52 bg-bg-panel border-r border-border flex flex-col shrink-0">
      {/* Logo */}
      <div className="h-11 flex items-center px-4 border-b border-border">
        <PenTool size={15} className="text-brand" />
        <span className="ml-2 text-xs text-text-primary tracking-wide" style={{ fontWeight: 510 }}>NovelForge</span>
        <span className="ml-auto text-2xs text-text-disabled font-mono">v7.4</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 p-2 space-y-px overflow-auto">
        {tabs.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          const disabled = !["overview", "models"].includes(t.id) && !selectedBookId;
          return (
            <button
              key={t.id}
              onClick={() => !disabled && setTab(t.id)}
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
        })}
      </nav>

      {/* New Book */}
      <div className="p-2 border-t border-border">
        <button
          onClick={onNewBook}
          className="btn-primary w-full flex items-center justify-center gap-1.5 py-[7px] text-xs rounded-md"
        >
          <Plus size={13} />
          新建项目
        </button>
      </div>
    </aside>
  );
}
