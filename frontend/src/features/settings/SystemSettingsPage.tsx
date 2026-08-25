import { useState } from "react";
import { ModelSetupPage } from "../model-setup/ModelSetupPage";
import { ContextInspector } from "../../components/ContextInspector";
import { GenreProfilePanel } from "../../components/GenreProfilePanel";
import { ResearchPanel } from "../../components/ResearchPanel";
import { PromptStudioPage } from "../prompt-studio/PromptStudioPage";
import { ResourceBar } from "../../components/ResourceBar";
import { useStore } from "../../store";
import { Cpu, Package, Palette, Globe, Sparkles, Activity } from "lucide-react";
import clsx from "clsx";

type SysTab = "models" | "context" | "genre" | "research" | "prompts" | "resources";

const TABS: { id: SysTab; label: string; icon: typeof Cpu; tip: string }[] = [
  { id: "models", label: "模型配置", icon: Cpu, tip: "自动检测 / 一键智能配置 / 性能与高级手动" },
  { id: "context", label: "Context", icon: Package, tip: "无书可进；有书时检视上下文包" },
  { id: "genre", label: "文风 Genre", icon: Palette, tip: "无书可进；有书时上传参考样本" },
  { id: "research", label: "调研", icon: Globe, tip: "无书可进；有书时管理调研会话" },
  { id: "prompts", label: "提示词工坊", icon: Sparkles, tip: "合同门禁 · 激活 fail-closed" },
  { id: "resources", label: "资源", icon: Activity, tip: "内存 / swap 快照" },
];

/**
 * System settings hub — always reachable without a selected book.
 * Sub-tabs: models (dropdown from /api/models/available), context, genre, research, prompts.
 */
export function SystemSettingsPage({ initialTab = "models" }: { initialTab?: SysTab }) {
  const [sub, setSub] = useState<SysTab>(initialTab);
  const selectedBookId = useStore((s) => s.selectedBookId);
  const books = useStore((s) => s.books);
  const selectBook = useStore((s) => s.selectBook);

  const bookOptions = books.map((b: any) => ({
    id: b.book_id || b.id,
    title: b.title || "未命名",
  }));

  return (
    <div className="h-full overflow-auto space-y-4 p-1">
      <div>
        <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
          系统设置
        </h2>
        <p className="text-xs text-text-tertiary mt-0.5">
          无需选中作品即可进入 · 模型下拉自动从 New-API /v1/models 拉取
        </p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = sub === t.id;
          return (
            <button
              key={t.id}
              type="button"
              title={t.tip}
              onClick={() => setSub(t.id)}
              className={clsx(
                "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs border transition-all",
                active
                  ? "bg-brand-muted border-brand/40 text-brand-accent"
                  : "bg-bg-panel border-border text-text-tertiary hover:text-text-secondary hover:border-border"
              )}
            >
              <Icon size={12} />
              {t.label}
            </button>
          );
        })}
      </div>

      {(sub === "context" || sub === "genre" || sub === "research") && (
        <div className="flex items-center gap-2 panel-elevated rounded-md px-3 py-2">
          <span className="text-2xs text-text-disabled shrink-0">当前作品（可选）</span>
          <select
            className="flex-1 max-w-md bg-bg-canvas border border-border rounded px-2 py-1 text-xs text-text-secondary focus:outline-none focus:border-brand"
            value={selectedBookId || ""}
            onChange={(e) => selectBook(e.target.value ? e.target.value : null)}
          >
            <option value="">— 不选书，仅浏览空态 —</option>
            {bookOptions.map((b) => (
              <option key={b.id} value={b.id}>
                {b.title}
              </option>
            ))}
          </select>
          {!selectedBookId && (
            <span className="text-2xs text-amber-400/90">部分操作需选书后可用</span>
          )}
        </div>
      )}

      <div className="min-h-[320px]">
        {sub === "models" && <ModelSetupPage />}
        {sub === "context" && <ContextInspector bookId={selectedBookId || ""} />}
        {sub === "genre" && <GenreProfilePanel bookId={selectedBookId || ""} />}
        {sub === "research" && <ResearchPanel bookId={selectedBookId || ""} />}
        {sub === "prompts" && <PromptStudioPage />}
        {sub === "resources" && (
          <div className="panel-elevated rounded-lg p-4 space-y-3">
            <p className="text-xs text-text-tertiary">主机资源快照（只读）</p>
            <ResourceBar />
          </div>
        )}
      </div>
    </div>
  );
}
