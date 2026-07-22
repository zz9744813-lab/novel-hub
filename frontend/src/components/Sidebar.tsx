import { BookOpen, GitGraph, FileText, Brain, AlertTriangle, Plus, PenTool } from "lucide-react";
import clsx from "clsx";

interface Props {
  tab: string;
  setTab: (t: any) => void;
  onNewBook: () => void;
}

const tabs = [
  { id: "overview", label: "项目总览", icon: BookOpen, desc: "Ke" },
  { id: "outline", label: "大纲依赖", icon: GitGraph, desc: "DAG" },
  { id: "chapters", label: "章节流水线", icon: FileText, desc: "Pipeline" },
  { id: "memory", label: "记忆银行", icon: Brain, desc: "L0-L4" },
  { id: "audit", label: "漂移审计", icon: AlertTriangle, desc: "DriftAudit" },
];

export function Sidebar({ tab, setTab, onNewBook }: Props) {
  return (
    <aside className="w-60 bg-ink-900 border-r border-ink-800 flex flex-col">
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b border-ink-800">
        <PenTool size={20} className="text-accent" />
        <span className="ml-2 text-sm font-bold text-gradient">NovelForge</span>
        <span className="ml-auto text-[10px] text-ink-500 font-mono">v7.3</span>
      </div>

      {/* Tabs */}
      <nav className="flex-1 p-3 space-y-0.5">
        {tabs.map((t) => {
          const Icon = t.icon;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={clsx(
                "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all",
                active
                  ? "bg-ink-800 text-accent border border-ink-700"
                  : "text-ink-400 hover:bg-ink-850 border border-transparent"
              )}
            >
              <Icon size={16} className={active ? "text-accent" : "text-ink-500"} />
              <div className="text-left">
                <div className={active ? "font-medium" : "font-normal"}>{t.label}</div>
                <div className="text-[10px] text-ink-500 font-mono">{t.desc}</div>
              </div>
            </button>
          );
        })}
      </nav>

      {/* New Book */}
      <div className="p-3 border-t border-ink-800">
        <button
          onClick={onNewBook}
          className="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg text-sm text-ink-300 bg-ink-800 hover:bg-ink-700 border border-ink-700 transition-all"
        >
          <Plus size={16} className="text-accent" />
          新建项目
        </button>
      </div>
    </aside>
  );
}