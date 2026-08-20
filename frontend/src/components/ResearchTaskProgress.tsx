import { Loader2 } from "lucide-react";

interface ResearchTaskProgressProps {
  taskId: string;
  status: "pending" | "running" | "completed" | "failed";
  progress: number;
  chaptersScraped: number;
  errorMessage?: string;
}

export function ResearchTaskProgress({
  taskId,
  status,
  progress,
  chaptersScraped,
  errorMessage,
}: ResearchTaskProgressProps) {
  const getStatusColor = () => {
    switch (status) {
      case "completed":
        return "bg-success";
      case "failed":
        return "bg-danger";
      case "running":
        return "bg-brand-accent animate-pulse";
      default:
        return "bg-text-disabled";
    }
  };

  const getStatusText = () => {
    switch (status) {
      case "completed":
        return "已完成";
      case "failed":
        return "失败";
      case "running":
        return "爬取中...";
      default:
        return "等待中";
    }
  };

  return (
    <div className="panel rounded-md p-4 space-y-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-text-primary font-medium">{getStatusText()}</span>
        <span className="text-xs text-text-tertiary font-mono">{taskId}</span>
      </div>

      {/* Progress bar */}
      <div className="relative h-3 bg-bg-surface rounded-full overflow-hidden">
        <div
          className={`h-full transition-all duration-300 ${getStatusColor()}`}
          style={{ width: `${progress}%` }}
        />
        
        {/* Centered text */}
        {status === "running" && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 size={12} className="animate-spin" />
          </div>
        )}
      </div>

      {/* Stats */}
      <div className="flex items-center justify-between text-xs text-text-tertiary">
        <span>{chaptersScraped} 章已抓取</span>
        <span className="font-mono">{progress}%</span>
      </div>

      {/* Error details (expandable in production) */}
      {status === "failed" && errorMessage && (
        <details className="mt-2 text-xs text-danger bg-danger/10 border border-danger/20 rounded-md p-2">
          <summary className="cursor-pointer font-medium">错误详情</summary>
          <p className="mt-1 break-words whitespace-pre-wrap">{errorMessage}</p>
        </details>
      )}
    </div>
  );
}
