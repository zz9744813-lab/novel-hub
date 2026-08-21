import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { Loader2, Globe, Play, CheckCircle, XCircle, AlertTriangle, BookOpen } from "lucide-react";
import { SourceSelector } from "../../components/SourceSelector";
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
  
  // Form validation helpers
  const validateForm = (): boolean => {
    const errors: {source?: string; url?: string} = {};
    
    // Source validation
    if (!newSourceName) {
      errors.source = "请选择调研源";
    } else if (!sources.some(s => s.name === newSourceName)) {
      errors.source = "无效的调研源";
    }
    
    // URL validation
    if (!newSourceUrl.trim()) {
      errors.url = "请输入目标 URL";
    } else {
      try {
        new URL(newSourceUrl.trim());
      } catch {
        errors.url = "URL 格式不正确，请以 https:// 开头";
      }
    }
    
    setValidationErrors(errors);
    return Object.keys(errors).length === 0;
  };
  const [validationErrors, setValidationErrors] = useState<{source?: string; url?: string}>({});

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
    if (!validateForm()) return;

    setCreatingTask(newSourceName);
    setError(null);
    
    try {
      const response = await fetch("/api/research/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_id: newSourceName,
          target_url: newSourceUrl.trim(),
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "创建任务失败");
      }

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
          setError(e instanceof Error ? e.message : "轮询错误");
        }
      }, 2000);

      setPollingInterval(interval);

      // Reset form on success
      setNewSourceUrl("");
    } catch (e: any) {
      // Show structured error message
      const errorMsg = e.message || String(e);
      if (errorMsg.includes("not found")) {
        setError("未找到指定的调研源配置");
      } else if (errorMsg.includes("401") || errorMsg.includes("unauthorized")) {
        setError("认证失败，请检查 Token");
      } else if (errorMsg.includes("network") || errorMsg.includes("fetch")) {
        setError("网络连接超时，请检查服务器是否运行");
      } else {
        setError(errorMsg);
      }
      console.error("Task creation error:", e);
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
      <div className="panel-elevated rounded-card p-5 space-y-4 animate-fade-in" style={{ animationDuration: "200ms" }}>
        <h3 className="text-sm text-text-primary font-medium flex items-center gap-2">
          <Play size={16} className="text-brand-accent" />
          新建调研任务
        </h3>
        
        <div className="space-y-4">
          <SourceSelector
            value={newSourceName}
            onChange={setNewSourceName}
            disabled={!!creatingTask}
            sources={sources}
          />
          
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">
              目标 URL
            </label>
            <input
              type="url"
              placeholder="https://example.com/novel/chapters"
              value={newSourceUrl}
              onChange={(e) => setNewSourceUrl(e.target.value)}
              disabled={!!creatingTask}
              className="w-full input text-xs py-2.5 px-3 focus:ring-2 focus:ring-brand-muted focus:border-brand-accent transition-all duration-150"
            />
            <p className="text-2xs text-text-disabled mt-1.5 flex items-center gap-1">
              <Globe size={10} />
              支持小说章节列表页或单章详情页
            </p>
          </div>
          
          <button
            onClick={handleCreateTask}
            disabled={!newSourceName || !newSourceUrl || !!creatingTask}
            className="btn-primary w-full py-2.5 px-3 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed shadow-lg hover:shadow-xl transition-all duration-200 transform hover:-translate-y-0.5"
          >
            {creatingTask ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                创建中...
              </>
            ) : (
              <>
                <Play size={14} />
                开始调研
              </>
            )}
          </button>
        </div>
      </div>

      {/* Task list header */}
      <h2 className="text-sm text-text-secondary font-medium mb-3 flex items-center gap-2">
        <Loader2 size={14} className="animate-spin" style={{ animationDuration: "3s" }} />
        正在进行的任务
      </h2>
      
      {/* Error notification */}
      {error && (
        <div className="flex items-start gap-2 px-4 py-3 bg-red-400/10 border border-red-400/20 rounded-md animate-fade-in">
          <AlertTriangle size={16} className="text-danger shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <p className="text-xs text-danger font-medium">{error}</p>
            <button
              onClick={() => setError(null)}
              className="mt-1 text-xs text-text-secondary underline hover:text-text-primary"
            >
              关闭提示
            </button>
          </div>
        </div>
      )}
      
      {/* Task list section */}
      <div className="space-y-3">
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
            {tasks.map((task, index) => (
              <div
                key={task.id}
                className="panel-elevated rounded-card p-4 transition-all duration-200 hover:shadow-lg animate-modal-in"
                style={{ animationDelay: `${index * 50}ms` }}
              >
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
                    <div className="flex items-center gap-3 mb-2">
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
