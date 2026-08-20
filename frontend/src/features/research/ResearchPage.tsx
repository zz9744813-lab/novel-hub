import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { Loader2, Globe, Play, CheckCircle, XCircle } from "lucide-react";
interface ResearchSource {
  name: string;
  base_url: string;
  chapter_list_selector: string;
  title_selector: string;
  content_selector: string;
  pagination_selector?: string;
  output_format: "epub" | "pdf" | "txt";
  encoding: string;
  rate_limit: number;
  description?: string;
}

interface ResearchTaskInfo {
  id: string;
  source_id: string;
  target_url: string;
  status: "pending" | "running" | "completed" | "failed";
  progress: number;
  chapters_scraped: number;
  error_message?: string;
  created_at: string;
  updated_at: string;
}

export function ResearchPage({ bookId }: { bookId?: string }) {
  const [sources, setSources] = useState<ResearchSource[]>([]);
  const [tasks, setTasks] = useState<ResearchTaskInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Form state
  const [newSourceName, setNewSourceName] = useState("");
  const [newSourceUrl, setNewSourceUrl] = useState("");
  const [creatingTask, setCreatingTask] = useState<string | null>(null);
  const [pollingInterval, setPollingInterval] = useState<number | null>(null);

  const loadSources = useCallback(async () => {
    try {
      // TODO: Call actual /api/research/sources endpoint
      // For now, use mock data
      const mockSources: ResearchSource[] = [
        {
          name: "起点中文网",
          base_url: "https://www.qidian.com",
          chapter_list_selector: "ul.send-list li a",
          title_selector: "h1.title",
          content_selector: "div.chapter-content",
          pagination_selector: "a.next-page",
          output_format: "txt",
          encoding: "utf-8",
          rate_limit: 1.0,
          description: "Mainstream Chinese web novel platform",
        },
      ];
      setSources(mockSources);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      // TODO: Call /api/research/tasks endpoint
      setTasks([]); // Mock empty initially
    } catch (e: any) {
      console.error("Failed to load tasks:", e);
    }
  }, []);

  useEffect(() => {
    loadSources();
    loadTasks();
  }, [loadSources, loadTasks]);

  const handleCreateTask = async () => {
    if (!newSourceName || !newSourceUrl) return;

    setCreatingTask(newSourceName);
    
    try {
      // TODO: Call /api/research/tasks POST endpoint
      const response = await fetch("/api/research/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_id: newSourceName,
          target_url: newSourceUrl,
        }),
      });

      if (!response.ok) throw new Error("Failed to create task");

      const task: ResearchTaskInfo = await response.json();
      setTasks((prev) => [task, ...prev]);

      // Start polling for updates
      const interval = setInterval(async () => {
        try {
          const taskResp = await fetch(`/api/research/tasks/${task.id}`);
          if (taskResp.ok) {
            const updatedTask: ResearchTaskInfo = await taskResp.json();
            setTasks((prev) =>
              prev.map((t) => (t.id === task.id ? updatedTask : t))
            );

            // Stop polling when done
            if (["completed", "failed"].includes(updatedTask.status)) {
              clearInterval(interval);
            }
          }
        } catch (e) {
          clearInterval(interval);
        }
      }, 2000);

      setPollingInterval(interval);

      // Reset form
      setNewSourceUrl("");
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setCreatingTask(null);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "completed":
        return <CheckCircle size={14} className="text-success" />;
      case "failed":
        return <XCircle size={14} className="text-danger" />;
      case "running":
        return <Loader2 size={14} className="animate-spin text-brand-accent" />;
      default:
        return <Globe size={14} className="text-text-disabled" />;
    }
  };

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-5">
      <div>
        <h1 className="text-base text-text-primary" style={{ fontWeight: 510 }}>
          外部调研
        </h1>
        <p className="text-xs text-text-tertiary mt-0.5">
          从外部网站爬取结构化内容，导入参考资料库
        </p>
      </div>

      {/* Create new task form */}
      <div className="panel-elevated rounded-md p-4 space-y-3">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="选择调研源（如：起点中文网）"
            value={newSourceName}
            onChange={(e) => setNewSourceName(e.target.value)}
            list="source-options"
            className="flex-1 input text-xs"
            disabled={!!creatingTask}
          />
          <datalist id="source-options">
            {sources.map((s) => (
              <option key={s.name} value={s.name} />
            ))}
          </datalist>
        </div>
        <input
          type="url"
          placeholder="目标 URL（小说章节列表页或详情页）"
          value={newSourceUrl}
          onChange={(e) => setNewSourceUrl(e.target.value)}
          className="w-full input text-xs"
          disabled={!!creatingTask}
        />
        <button
          onClick={handleCreateTask}
          disabled={!newSourceName || !newSourceUrl || !!creatingTask}
          className="btn-primary w-full py-2 px-3 text-xs flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {creatingTask ? (
            <>
              <Loader2 size={14} className="animate-spin" /> 创建中...
            </>
          ) : (
            <>
              <Play size={14} /> 开始调研
            </>
          )}
        </button>
      </div>

      {/* Task list */}
      <div className="space-y-3">
        <h2 className="text-sm text-text-secondary font-medium">正在进行的任务</h2>
        
        {loading ? (
          <div className="flex items-center justify-center py-8 text-text-tertiary text-xs gap-2">
            <Loader2 size={16} className="animate-spin" /> 加载中…
          </div>
        ) : tasks.length === 0 ? (
          <div className="panel-elevated rounded-md px-4 py-10 text-center space-y-2">
            <p className="text-xs text-text-tertiary">暂无调研任务</p>
          </div>
        ) : (
          <div className="space-y-2">
            {tasks.map((task) => (
              <div key={task.id} className="panel-elevated rounded-md p-3.5">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5">{getStatusIcon(task.status)}</div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs text-text-primary font-medium truncate">
                        {task.source_id}
                      </span>
                      <span className="text-2xs text-text-disabled font-mono">{task.id}</span>
                    </div>
                    
                    <div className="text-xs text-text-tertiary truncate mb-2">
                      {task.target_url}
                    </div>
                    
                    {/* Progress bar */}
                    <div className="flex items-center gap-3">
                      <div className="flex-1 h-2 bg-bg-surface rounded-full overflow-hidden">
                        <div
                          className={`h-full ${
                            task.status === "completed"
                              ? "bg-success"
                              : task.status === "failed"
                              ? "bg-danger"
                              : "bg-brand-accent animate-pulse"
                          }`}
                          style={{ width: `${task.progress}%` }}
                        />
                      </div>
                      <span className="text-2xs text-text-disabled whitespace-nowrap">
                        {task.chapters_scraped}章 · {task.progress}%
                      </span>
                    </div>

                    {/* Error message */}
                    {task.status === "failed" && task.error_message && (
                      <p className="text-xs text-danger mt-2">{task.error_message}</p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
