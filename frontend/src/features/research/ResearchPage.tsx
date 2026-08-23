import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../../api";
import type { ResearchProbeResult, ResearchScrapeSource, ResearchScrapeTask } from "../../api";
import {
  Loader2,
  Globe,
  Play,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Ban,
  RefreshCw,
  Download,
  BookPlus,
  Zap,
  ScrollText,
  FlaskConical,
} from "lucide-react";
import { SourceSelector } from "../../components/SourceSelector";
import { ResearchPanel } from "../../components/ResearchPanel";
import { SourceDiagnosticsPanel } from "./SourceDiagnosticsPanel";
import clsx from "clsx";

type WorkbenchTab = "collect" | "topic" | "sources";

const ACTIVE_STATUSES = ["queued", "running", "cancel_requested"];
const POLL_INTERVAL_MS = 2000;

function statusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "排队中";
    case "running":
      return "抓取中";
    case "cancel_requested":
      return "取消中";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "cancelled":
      return "已取消";
    default:
      return status;
  }
}

function errorMessage(task: ResearchScrapeTask): string | null {
  if (task.status !== "failed") return null;
  const detail =
    task.error_detail?.detail != null ? String(task.error_detail.detail) : null;
  if (task.error_code && detail) return `${task.error_code}: ${detail}`;
  if (task.error_code) return task.error_code;
  return detail || "任务失败";
}

export function ResearchPage({ bookId }: { bookId?: string }) {
  const [sources, setSources] = useState<ResearchScrapeSource[]>([]);
  const [tasks, setTasks] = useState<ResearchScrapeTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [creating, setCreating] = useState(false);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const [importingId, setImportingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<ResearchProbeResult | null>(null);
  const [tab, setTab] = useState<WorkbenchTab>("collect");

  const handleProbeFromDiagnostics = useCallback((source: ResearchScrapeSource) => {
    setSelectedSourceId(source.id);
    setTargetUrl("");
    setProbeResult(null);
    setTab("collect");
  }, []);

  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadSources = useCallback(async () => {
    try {
      const data = await api.researchScrape.sources();
      setSources(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadTasks = useCallback(
    async (silent = false) => {
      if (!silent) setRefreshing(true);
      try {
        const data = await api.researchScrape.tasks({
          book_id: bookId,
          limit: 50,
        });
        setTasks(data.tasks);
      } catch (e) {
        if (!silent) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!silent) setRefreshing(false);
      }
    },
    [bookId]
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await loadSources();
      if (!cancelled) setLoading(false);
    })();
    loadTasks();
    return () => {
      cancelled = true;
    };
  }, [loadSources, loadTasks]);

  const hasActive = tasks.some((t) => ACTIVE_STATUSES.includes(t.status));

  useEffect(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    if (!hasActive) return;
    pollingRef.current = setInterval(() => {
      loadTasks(true);
    }, POLL_INTERVAL_MS);
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [hasActive, loadTasks]);

  const selectedSource = sources.find((s) => s.id === selectedSourceId);

  const handleCreateTask = async () => {
    if (!selectedSourceId || !targetUrl.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const task = await api.researchScrape.createTask({
        source_id: selectedSourceId,
        target_url: targetUrl.trim(),
        book_id: bookId,
      });
      setTasks((prev) => [task, ...prev]);
      setTargetUrl("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const handleProbe = async () => {
    if (!selectedSourceId || !targetUrl.trim()) return;
    setProbing(true);
    setError(null);
    setProbeResult(null);
    try {
      const result = await api.researchScrape.probe(
        selectedSourceId,
        targetUrl.trim()
      );
      setProbeResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setProbing(false);
    }
  };

  const handleCancel = async (taskId: string) => {
    setCancellingId(taskId);
    setError(null);
    try {
      const updated = await api.researchScrape.cancelTask(taskId);
      setTasks((prev) => prev.map((t) => (t.id === taskId ? updated : t)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCancellingId(null);
    }
  };

  const handleExport = async (taskId: string) => {
    setExportingId(taskId);
    setError(null);
    setNotice(null);
    try {
      const exp = await api.researchScrape.exportTask(taskId);
      await api.researchScrape.exportDownload(exp.id, `research-${taskId.slice(0, 8)}`);
      setNotice(`已导出 ${exp.document_count} 章为 TXT（${exp.byte_size} 字节）`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExportingId(null);
    }
  };

  const handleImportReference = async (taskId: string) => {
    if (!bookId) return;
    setImportingId(taskId);
    setError(null);
    setNotice(null);
    try {
      const result = await api.researchScrape.importReference(taskId, {
        book_id: bookId,
        mode: "all",
      });
      setNotice(
        `已导入参考资料：新建 ${result.created} 个样本` +
          (result.deduped > 0 ? `，去重 ${result.deduped} 个` : "")
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImportingId(null);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "completed":
        return <CheckCircle size={14} className="text-success" />;
      case "failed":
        return <XCircle size={14} className="text-danger" />;
      case "cancelled":
        return <Ban size={14} className="text-text-disabled" />;
      case "running":
      case "cancel_requested":
        return <Loader2 size={14} className="animate-spin text-brand-accent" />;
      default:
        return <Globe size={14} className="text-text-disabled" />;
    }
  };

  const urlInvalid =
    targetUrl.trim().length > 0 && !/^https?:\/\/\S+\.\S+/.test(targetUrl.trim());

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-base text-text-primary" style={{ fontWeight: 510 }}>
          调研工作台
        </h1>
        <div className="ml-auto flex items-center rounded-control border border-border bg-bg-surface p-0.5">
          <TabButton active={tab === "topic"} onClick={() => setTab("topic")} icon={<ScrollText size={13} />} label="主题调研" />
          <TabButton active={tab === "collect"} onClick={() => setTab("collect")} icon={<Globe size={13} />} label="参考作品采集" />
          <TabButton active={tab === "sources"} onClick={() => setTab("sources")} icon={<FlaskConical size={13} />} label="书源诊断" />
        </div>
      </div>

      {tab === "topic" && <ResearchPanel bookId={bookId || ""} />}
      {tab === "sources" && <SourceDiagnosticsPanel onProbeSource={handleProbeFromDiagnostics} />}

      {tab === "collect" && (
        <>
      {/* Create new task form */}
      <div
        className="panel-elevated rounded-card p-5 space-y-4 animate-fade-in"
        style={{ animationDuration: "200ms" }}
      >
        <h3 className="text-sm text-text-primary font-medium flex items-center gap-2">
          <Play size={16} className="text-brand-accent" />
          新建调研任务
        </h3>

        <div className="space-y-4">
          {loading ? (
            <div className="flex items-center justify-center py-6 text-text-tertiary text-xs gap-2">
              <Loader2 size={16} className="animate-spin" /> 加载调研源…
            </div>
          ) : sources.length === 0 ? (
            <div className="px-4 py-6 text-center text-xs text-text-tertiary">
              暂无可用调研源，请检查后端书源配置
            </div>
          ) : (
            <SourceSelector
              value={selectedSourceId}
              onChange={setSelectedSourceId}
              disabled={!!creating}
              sources={sources}
            />
          )}

          {selectedSource?.verification_status === "experimental" && (
            <p className="text-2xs text-warning flex items-center gap-1">
              <AlertTriangle size={10} />
              该书源选择器处于实验状态，抓取可能失败
            </p>
          )}

          <div>
            <label className="block text-xs font-medium text-text-secondary mb-1.5">
              目标 URL
            </label>
            <input
              type="url"
              placeholder="请粘贴真实作品目录页或章节页 URL"
              value={targetUrl}
              onChange={(e) => {
                setTargetUrl(e.target.value);
                setProbeResult(null);
              }}
              disabled={!!creating}
              className="w-full input text-xs py-2.5 px-3 focus:ring-2 focus:ring-brand-muted focus:border-brand-accent transition-all duration-150"
            />
            {urlInvalid && (
              <p className="text-2xs text-danger mt-1.5">
                URL 格式不正确，请以 http(s):// 开头
              </p>
            )}
            {!targetUrl.trim() && (
              <p className="text-2xs text-warning mt-1.5">
                请输入真实 URL 后才能开始
              </p>
            )}
            <p className="text-2xs text-text-disabled mt-1.5 flex items-center gap-1">
              <Globe size={10} />
              支持小说章节列表页或单章详情页
            </p>
          </div>

          {probeResult && <ProbeResultPanel result={probeResult} />}

          <div className="flex gap-2">
            <button
              onClick={handleProbe}
              disabled={!selectedSourceId || !targetUrl.trim() || urlInvalid || probing}
              className="btn flex-1 py-2.5 px-3 items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              title="先测试该地址能否解析，再决定是否采集"
            >
              {probing ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  测试中...
                </>
              ) : (
                <>
                  <Zap size={14} />
                  测试该地址
                </>
              )}
            </button>
            <button
              onClick={handleCreateTask}
              disabled={!selectedSourceId || !targetUrl.trim() || urlInvalid || !!creating}
              className="btn-primary flex-1 py-2.5 px-3 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed shadow-lg hover:shadow-xl transition-all duration-200 transform hover:-translate-y-0.5"
            >
              {creating ? (
                <>
                  <Loader2 size={14} className="animate-spin" />
                  创建中...
                </>
              ) : (
                <>
                  <Play size={14} />
                  开始采集
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Task list header */}
      <h2 className="text-sm text-text-secondary font-medium mb-3 flex items-center gap-2">
        {hasActive ? (
          <Loader2 size={14} className="animate-spin" style={{ animationDuration: "3s" }} />
        ) : (
          <CheckCircle size={14} className="text-text-disabled" />
        )}
        调研任务
        <span className="text-2xs text-text-disabled font-normal">
          （{tasks.length}）
        </span>
      </h2>

      {/* Notice notification */}
      {notice && (
        <div className="flex items-start gap-2 px-4 py-3 bg-emerald-400/10 border border-emerald-400/20 rounded-md animate-fade-in">
          <CheckCircle size={16} className="text-success shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <p className="text-xs text-success font-medium break-all">{notice}</p>
            <button
              onClick={() => setNotice(null)}
              className="mt-1 text-xs text-text-secondary underline hover:text-text-primary"
            >
              关闭提示
            </button>
          </div>
        </div>
      )}

      {/* Error notification */}
      {error && (
        <div className="flex items-start gap-2 px-4 py-3 bg-red-400/10 border border-red-400/20 rounded-md animate-fade-in">
          <AlertTriangle size={16} className="text-danger shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <p className="text-xs text-danger font-medium break-all">{error}</p>
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
            {tasks.map((task, index) => {
              const active = ACTIVE_STATUSES.includes(task.status);
              return (
                <div
                  key={task.id}
                  className="panel-elevated rounded-card p-4 transition-all duration-200 hover:shadow-lg animate-modal-in"
                  style={{ animationDelay: `${index * 50}ms` }}
                >
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5">{getStatusIcon(task.status)}</div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1 flex-wrap">
                        <span className="text-xs text-text-primary font-medium truncate">
                          {task.source_name || task.source_code || task.source_id.slice(0, 8)}
                        </span>
                        <span className="text-2xs px-1.5 py-0.5 rounded bg-bg-surface text-text-secondary border border-border">
                          {statusLabel(task.status)}
                        </span>
                        <span className="text-2xs text-text-disabled font-mono">
                          {task.id.slice(0, 8)}
                        </span>
                      </div>

                      <div className="text-xs text-text-tertiary truncate mb-2">
                        {task.target_url}
                      </div>

                      {/* Progress bar */}
                      <div className="flex items-center gap-3 mb-2">
                        <div className="flex-1 h-2 bg-bg-surface rounded-full overflow-hidden">
                          <div
                            className={
                              task.status === "completed"
                                ? "h-full bg-success"
                                : task.status === "failed"
                                  ? "h-full bg-danger"
                                  : task.status === "cancelled"
                                    ? "h-full bg-text-disabled"
                                    : "h-full bg-brand-accent animate-pulse"
                            }
                            style={{ width: `${Math.min(100, Math.max(0, task.progress))}%` }}
                          />
                        </div>
                        <span className="text-2xs text-text-disabled whitespace-nowrap">
                          {task.completed_count}章 · {task.progress}%
                        </span>
                      </div>

                      {/* Current url while running */}
                      {task.status === "running" && task.current_url && (
                        <p className="text-2xs text-text-disabled truncate mb-1 flex items-center gap-1">
                          <Globe size={10} />
                          {task.current_url}
                        </p>
                      )}

                      {/* Error message */}
                      {errorMessage(task) && (
                        <p className="text-xs text-danger mt-2 break-all">
                          {errorMessage(task)}
                        </p>
                      )}

                      {/* Actions */}
                      {active ? (
                        <button
                          onClick={() => handleCancel(task.id)}
                          disabled={cancellingId === task.id || task.status === "cancel_requested"}
                          className="mt-2 text-2xs text-text-secondary underline hover:text-danger disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                        >
                          {cancellingId === task.id ? (
                            <>
                              <Loader2 size={10} className="animate-spin" /> 处理中…
                            </>
                          ) : task.status === "cancel_requested" ? (
                            <>取消中…</>
                          ) : (
                            <>
                              <Ban size={10} /> 取消任务
                            </>
                          )}
                        </button>
                      ) : task.status === "completed" ? (
                        <div className="mt-2 flex items-center gap-3">
                          <button
                            onClick={() => handleExport(task.id)}
                            disabled={exportingId === task.id}
                            className="text-2xs text-text-secondary underline hover:text-brand-accent disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                          >
                            {exportingId === task.id ? (
                              <>
                                <Loader2 size={10} className="animate-spin" /> 导出中…
                              </>
                            ) : (
                              <>
                                <Download size={10} /> 导出 TXT
                              </>
                            )}
                          </button>
                          <button
                            onClick={() => handleImportReference(task.id)}
                            disabled={importingId === task.id || !bookId}
                            title={bookId ? "导入当前作品的参考资料库" : "请先在上方选择作品"}
                            className="text-2xs text-text-secondary underline hover:text-brand-accent disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
                          >
                            {importingId === task.id ? (
                              <>
                                <Loader2 size={10} className="animate-spin" /> 导入中…
                              </>
                            ) : (
                              <>
                                <BookPlus size={10} /> 导入参考资料
                              </>
                            )}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
        </>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "flex items-center gap-1 rounded px-2.5 py-1 text-2xs transition-all",
        active ? "bg-brand-muted text-brand-accent" : "text-text-tertiary hover:text-text-primary"
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function ProbeResultPanel({ result }: { result: ResearchProbeResult }) {
  const passed = result.status === "passed";
  const blocked = result.status === "blocked";
  const pageTypeLabel =
    result.page_type === "book"
      ? "章节目录"
      : result.page_type === "chapter"
        ? "正文页"
        : "未识别";

  return (
    <div
      className={
        passed
          ? "rounded-md border border-emerald-400/25 bg-emerald-400/5 px-3.5 py-3"
          : blocked
            ? "rounded-md border border-warning/25 bg-warning/5 px-3.5 py-3"
            : "rounded-md border border-red-400/25 bg-red-400/5 px-3.5 py-3"
      }
    >
      <div className="flex items-center gap-2 mb-1.5">
        {passed ? (
          <CheckCircle size={14} className="text-success shrink-0" />
        ) : blocked ? (
          <Ban size={14} className="text-warning shrink-0" />
        ) : (
          <XCircle size={14} className="text-danger shrink-0" />
        )}
        <span className="text-xs font-medium text-text-primary">
          {passed
            ? "当前地址可解析"
            : blocked
              ? "该地址被拦截 / 访问受限"
              : "当前地址无法解析"}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-2xs text-text-secondary">
        <span>HTTP {result.http_status ?? "—"}</span>
        <span>页面类型 {pageTypeLabel}</span>
        <span>目录链接 {result.list_link_count}</span>
        <span>标题命中 {result.title_hit_count}</span>
        <span>正文字符 {result.extracted_chars.toLocaleString()}</span>
        <span>耗时 {result.latency_ms ?? "—"}ms</span>
      </div>

      {result.anti_bot_type && (
        <p className="mt-1.5 text-2xs text-warning">
          检测到访问控制：{result.anti_bot_type}（请使用合法可访问的页面）
        </p>
      )}

      {!passed && result.candidate_selectors.length > 0 && (
        <div className="mt-2 pt-2 border-t border-border">
          <div className="text-2xs text-text-disabled mb-1">
            候选节点（当前规则未命中，建议更新规则后重试）：
          </div>
          <div className="flex flex-col gap-0.5">
            {result.candidate_selectors.map((c) => (
              <span key={c.selector} className="font-mono text-2xs text-text-secondary">
                {c.selector}{" "}
                <span className="text-text-disabled">({c.chars.toLocaleString()} 字)</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
