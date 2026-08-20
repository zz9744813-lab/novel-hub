export interface TokenMetrics {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  estimatedCost?: number; // in USD (example: $0.002 per 1K tokens for a typical model)
  updatedAt: string;
}

interface TokenMetricsDashboardProps {
  metrics: TokenMetrics;
  onRefresh?: () => void;
}

const formatNumber = (n: number): string => n.toLocaleString("zh-CN");
const formatCost = (c?: number): string => c ? `¥${c.toFixed(4)}` : "-";

export function TokenMetricsDashboard({ metrics, onRefresh }: TokenMetricsDashboardProps) {
  const chartHeight = 32;
  const maxTokens = Math.max(metrics.inputTokens, metrics.outputTokens);
  const inputPercent = maxTokens ? (metrics.inputTokens / maxTokens) * 100 : 0;
  const outputPercent = maxTokens ? (metrics.outputTokens / maxTokens) * 100 : 0;

  return (
    <div className="token-metrics panel-elevated rounded-card p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-emphasis text-text-primary" style={{ fontWeight: 590 }}>Token 消耗</h3>
        <button
          onClick={onRefresh}
          className="btn text-xs py-1 px-2 rounded-control"
          title="刷新"
        >
          ↻
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="stat-card bg-bg-surface border border-border p-3">
          <div className="text-caption text-text-disabled">输入</div>
          <div className="text-h2 text-text-primary font-semibold mt-1">
            {formatNumber(metrics.inputTokens)}
          </div>
        </div>
        <div className="stat-card bg-bg-surface border border-border p-3">
          <div className="text-caption text-text-disabled">输出</div>
          <div className="text-h2 text-text-primary font-semibold mt-1">
            {formatNumber(metrics.outputTokens)}
          </div>
        </div>
        <div className="stat-card bg-brand-muted/30 border border-brand/30 p-3">
          <div className="text-caption text-brand-accent">总计</div>
          <div className="text-h2 text-brand-accent font-semibold mt-1">
            {formatNumber(metrics.totalTokens)}
          </div>
        </div>
      </div>

      {/* Visual bar chart */}
      {maxTokens > 0 && (
        <div className="mb-4">
          <div className="flex items-center justify-between text-caption text-text-disabled mb-1.5">
            <span>分布</span>
            <span>输入 vs 输出</span>
          </div>
          <div className="flex h-[32px] rounded-control overflow-hidden border border-border">
            <div
              className="bg-success"
              style={{ width: `${inputPercent}%`, minWidth: "8%" }}
            />
            <div
              className="bg-info"
              style={{ width: `${outputPercent}%`, minWidth: "8%" }}
            />
          </div>
        </div>
      )}

      {/* Cost estimate */}
      <div className="text-caption text-text-disabled flex items-center justify-between border-t border-border pt-3 mt-3">
        <span>成本估算</span>
        <span className="text-text-primary font-medium">{formatCost(metrics.estimatedCost)}</span>
      </div>
    </div>
  );
}
