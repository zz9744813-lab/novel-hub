import { useEffect, useState } from "react";
import { api, type TaskItem } from "../../api";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Clock, Loader2, Pause, Play, RefreshCw, Square, Upload, PenTool, Search } from "lucide-react";

function displayDetail(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "object") {
    const detail = value as { message?: unknown };
    if (typeof detail.message === "string") return detail.message;
    try { return JSON.stringify(value); } catch { return "详情不可用"; }
  }
  return String(value);
}

function statusIcon(status: string) {
  if (["completed", "succeeded", "finalized", "preview_ready", "suggested"].includes(status)) {
    return <CheckCircle2 size={13} className="text-emerald-400" />;
  }
  if (["failed", "needs_human", "resource_blocked", "blocked_by_dependency"].includes(status)) {
    return <AlertTriangle size={13} className="text-amber-400" />;
  }
  if (["analyzing", "running", "drafting", "planning", "searching", "synthesizing"].includes(status)) {
    return <Loader2 size={13} className="animate-spin text-brand-accent" />;
  }
  return <Clock size={13} className="text-text-disabled" />;
}

function taskIcon(type: TaskItem["task_type"]) {
  if (type === "chapter") return <PenTool size={13} className="text-brand-accent" />;
  if (type === "import") return <Upload size={13} className="text-brand-accent" />;
  return <Search size={13} className="text-brand-accent" />;
}

const typeLabels: Record<TaskItem["task_type"], string> = {
  chapter: "章节生成",
  import: "导入会话",
  research: "调研会话",
};

const actionLabels: Record<string, string> = {
  pause: "暂停",
  resume: "恢复",
  cancel: "取消",
  retry: "重试",
};

function taskTitle(item: TaskItem): string {
  if (item.task_type === "chapter") return `第 ${item.chapter_no ?? "?"} 章`;
  if (item.task_type === "research") return item.topic || "调研任务";
  return item.current_step || "导入任务";
}

export function WritingTasksPage() {
  const [items, setItems] = useState<TaskItem[]>([]);
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyTask, setBusyTask] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.tasks.list({
        task_type: typeFilter || undefined,
        status: statusFilter || undefined,
        page,
        page_size: 50,
      });
      setItems(data.items || []);
      setPages(data.pages || 0);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [page, typeFilter, statusFilter]);

  const operate = async (item: TaskItem, action: string) => {
    setBusyTask(item.task_id);
    setError(null);
    try {
      await api.tasks.operate(item.task_id, action);
      await load();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setBusyTask(null);
    }
  };

  const activeCount = items.filter((item) => !["completed", "succeeded", "failed", "cancelled", "finalized"].includes(item.status)).length;
  const humanCount = items.filter((item) => item.status === "needs_human").length;

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-base text-text-primary" style={{ fontWeight: 510 }}>写作任务</h1>
          <p className="text-xs text-text-tertiary mt-0.5">统一任务中心 · 可筛选、分页和控制运行状态</p>
        </div>
        <button onClick={() => void load()} className="btn-ghost text-xs py-1.5 px-3 flex items-center gap-1.5" disabled={loading}>
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> 刷新
        </button>
      </div>

      <div className="panel-elevated rounded-lg p-3 flex flex-wrap items-center gap-2">
        <select value={typeFilter} onChange={(event) => { setTypeFilter(event.target.value); setPage(1); }} className="input text-xs py-1.5">
          <option value="">全部类型</option>
          <option value="chapter">章节生成</option>
          <option value="import">导入会话</option>
          <option value="research">调研会话</option>
        </select>
        <select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }} className="input text-xs py-1.5">
          <option value="">全部状态</option>
          <option value="running">运行中</option>
          <option value="paused">已暂停</option>
          <option value="needs_human">待人工</option>
          <option value="failed">失败</option>
          <option value="succeeded">已完成</option>
        </select>
        <span className="ml-auto text-2xs font-mono text-text-disabled">{total} 项 · {activeCount} 活跃 · {humanCount} 待人工</span>
      </div>

      {error && <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">{error}</div>}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-text-tertiary text-xs gap-2"><Loader2 size={16} className="animate-spin" /> 加载任务队列…</div>
      ) : items.length === 0 ? (
        <div className="panel-elevated rounded-lg p-8 text-center text-xs text-text-disabled">暂无符合条件的任务</div>
      ) : (
        <section className="panel-elevated rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-border flex items-center gap-2">
            <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>全部任务</span>
            <span className="ml-auto text-2xs font-mono text-text-disabled">第 {page} / {Math.max(pages, 1)} 页</span>
          </div>
          <div className="divide-y divide-border">
            {items.map((item) => (
              <div key={item.task_id} className="px-3 py-3 flex items-center gap-3">
                {statusIcon(item.status)}
                {taskIcon(item.task_type)}
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-2 text-xs text-text-primary">
                    <span>{taskTitle(item)}</span>
                    <span className="text-2xs text-text-disabled">{typeLabels[item.task_type]}</span>
                    {item.book_title && <span className="text-2xs text-text-disabled truncate">· {item.book_title}</span>}
                  </div>
                  <div className="text-2xs text-text-disabled font-mono truncate mt-0.5">
                    {item.status} · {displayDetail(item.current_step) || "等待处理"} · {item.task_id.slice(0, 18)}
                    {item.error?.code ? ` · ${item.error.code}` : ""}
                  </div>
                  {item.error && <div className="text-2xs text-amber-300/80 truncate mt-0.5">{displayDetail(item.error.detail)}</div>}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {item.actions.map((action) => (
                    <button
                      key={action}
                      className="btn-ghost text-2xs py-1 px-2 flex items-center gap-1"
                      disabled={busyTask === item.task_id}
                      onClick={() => void operate(item, action)}
                      title={actionLabels[action] || action}
                    >
                      {action === "pause" && <Pause size={11} />}
                      {action === "resume" && <Play size={11} />}
                      {action === "cancel" && <Square size={11} />}
                      {action === "retry" && <RefreshCw size={11} />}
                      {actionLabels[action] || action}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <div className="px-3 py-2 border-t border-border flex justify-end gap-2">
            <button className="btn-ghost text-xs py-1 px-2" disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}><ChevronLeft size={13} /></button>
            <button className="btn-ghost text-xs py-1 px-2" disabled={pages === 0 || page >= pages || loading} onClick={() => setPage((value) => value + 1)}><ChevronRight size={13} /></button>
          </div>
        </section>
      )}

      <p className="text-2xs text-text-disabled text-center pt-2">数据来自统一 /api/tasks 任务契约</p>
    </div>
  );
}
