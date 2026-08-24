import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import type { EditorialMetrics } from "../../api";
import { Loader2, RefreshCw, TrendingUp, Target, AlertTriangle, ShieldCheck, Layers } from "lucide-react";

const COMPONENT_LABELS: Record<string, string> = {
  chapter_planner: "章节规划",
  ccne: "因果引擎",
  memory: "记忆/上下文",
  voice: "人物声线",
  style: "文风",
  draft_writer: "正文撰写",
  review_agent: "AI 审校",
  patch_editor: "补丁编辑",
};

function MiniTrend({ points }: { points: Array<{ chapter_no: number | null; score: number | null }> }) {
  const data = points.filter((p) => p.score != null && p.chapter_no != null);
  if (data.length < 2) {
    return <p className="text-2xs text-text-disabled py-4 text-center">至少两章评分后显示趋势</p>;
  }
  const w = 320;
  const h = 64;
  const xs = data.map((_, i) => (i / (data.length - 1)) * (w - 8) + 4);
  const ys = data.map((p) => h - 4 - ((p.score! / 100) * (h - 10)));
  const path = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16">
      <line x1="4" y1={h - 4 - 85 * (h - 10) / 100} x2={w - 4} y2={h - 4 - 85 * (h - 10) / 100} stroke="rgb(var(--nf-success))55" strokeDasharray="3 3" strokeWidth="1" />
      <path d={path} fill="none" stroke="#7c8aff" strokeWidth="1.6" strokeLinejoin="round" />
      {xs.map((x, i) => (
        <circle key={i} cx={x} cy={ys[i]} r="2.4" fill={data[i].score! >= 85 ? "rgb(var(--nf-success))" : data[i].score! >= 70 ? "rgb(var(--nf-warning))" : "rgb(var(--nf-danger))"} />
      ))}
    </svg>
  );
}

function BarList({ items, labels }: { items: Record<string, number>; labels?: Record<string, string> }) {
  const entries = Object.entries(items);
  if (entries.length === 0) {
    return <p className="text-2xs text-text-disabled py-4 text-center">暂无数据</p>;
  }
  const max = Math.max(...entries.map(([, v]) => v), 1);
  return (
    <div className="space-y-2">
      {entries.map(([key, count]) => (
        <div key={key} className="space-y-1">
          <div className="flex items-center justify-between text-2xs">
            <span className="text-text-secondary">{labels?.[key] ?? key}</span>
            <span className="text-text-tertiary tabular-nums">{count}</span>
          </div>
          <div className="h-1.5 rounded-full bg-bg-panel/5 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-brand-accent/70 to-brand-accent transition-all duration-500"
              style={{ width: `${(count / max) * 100}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

export function QualityDashboard({ bookId }: { bookId: string }) {
  const [metrics, setMetrics] = useState<EditorialMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setRefreshing(true);
      try {
        setMetrics(await api.editorial.metrics(bookId));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!silent) setRefreshing(false);
      }
    },
    [bookId]
  );

  useEffect(() => {
    setLoading(true);
    load(true).finally(() => setLoading(false));
  }, [load]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-text-tertiary text-xs gap-2">
        <Loader2 size={16} className="animate-spin" /> 加载质量指标…
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel rounded-lg px-4 py-3 text-xs text-danger">{error}</div>
    );
  }

  if (!metrics) return null;

  const cal = metrics.ai_calibration;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm text-text-secondary font-medium flex items-center gap-2">
          <TrendingUp size={14} className="text-brand-accent" />
          质量看板
        </h2>
        <button
          onClick={() => load()}
          disabled={refreshing}
          className="btn-ghost px-2.5 py-1.5 flex items-center gap-1.5 text-xs disabled:opacity-50"
        >
          <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} />
          刷新
        </button>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="stat-card">
          <div className="stat-label">
            <Target size={11} /> 首轮良品率
          </div>
          <div className="stat-value" style={{ color: metrics.first_pass_yield == null ? undefined : metrics.first_pass_yield >= 60 ? "rgb(var(--nf-success))" : "rgb(var(--nf-warning))" }}>
            {metrics.first_pass_yield == null ? "—" : `${metrics.first_pass_yield}%`}
          </div>
          <div className="text-2xs text-text-disabled mt-1">
            {metrics.first_pass_accepted}/{metrics.total_reviewed} 章首轮通过
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">
            <TrendingUp size={11} /> 近5章优良率
          </div>
          <div className="stat-value">
            {metrics.window_good_rate == null ? "—" : `${metrics.window_good_rate}%`}
          </div>
          <div className="text-2xs text-text-disabled mt-1">按分数≥85计算</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">
            <ShieldCheck size={11} /> AI 审校一致率
          </div>
          <div className="stat-value" style={{ color: cal.agreement == null ? undefined : cal.agreement >= 70 ? "rgb(var(--nf-success))" : "rgb(var(--nf-warning))" }}>
            {cal.agreement == null ? "—" : `${cal.agreement}%`}
          </div>
          <div className="text-2xs text-text-disabled mt-1">
            确认 {cal.confirmed} / 驳回 {cal.dismissed} / 修正 {cal.corrected}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">
            <AlertTriangle size={11} /> AI 漏报率
          </div>
          <div className="stat-value" style={{ color: cal.escape_rate == null ? undefined : cal.escape_rate <= 20 ? "rgb(var(--nf-success))" : "rgb(var(--nf-danger))" }}>
            {cal.escape_rate == null ? "—" : `${cal.escape_rate}%`}
          </div>
          <div className="text-2xs text-text-disabled mt-1">
            人工严重问题 {cal.severe_human_issues} 项，AI 漏报 {cal.escaped} 项
          </div>
        </div>
      </div>

      {metrics.consecutive_bad >= 2 && (
        <div className="panel rounded-lg px-4 py-3 text-xs text-warning flex items-center gap-2">
          <AlertTriangle size={14} />
          已连续 {metrics.consecutive_bad} 章评分为 C/D — 达到自动暂停线时流水线将暂停生成，建议检查根因分布
        </div>
      )}

      {/* trend + revision depth */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <div className="panel rounded-card p-4 lg:col-span-2">
          <h3 className="text-xs text-text-primary mb-2" style={{ fontWeight: 560 }}>
            章节评分趋势（绿线=85 优良线）
          </h3>
          <MiniTrend points={metrics.score_trend} />
        </div>
        <div className="panel rounded-card p-4">
          <h3 className="text-xs text-text-primary mb-3 flex items-center gap-1.5" style={{ fontWeight: 560 }}>
            <Layers size={12} className="text-brand-accent" /> 修订深度
          </h3>
          <BarList
            items={Object.fromEntries(
              Object.entries(metrics.revision_depth).map(([k, v]) => [
                k === "3" ? "3 轮以上" : `${k} 轮过`,
                v,
              ])
            )}
          />
        </div>
      </div>

      {/* pareto + root causes */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="panel rounded-card p-4">
          <h3 className="text-xs text-text-primary mb-3" style={{ fontWeight: 560 }}>
            问题类别 Pareto
          </h3>
          <BarList items={metrics.category_pareto} />
        </div>
        <div className="panel rounded-card p-4">
          <h3 className="text-xs text-text-primary mb-3" style={{ fontWeight: 560 }}>
            根因分布（组件归因）
          </h3>
          <BarList items={metrics.root_causes} labels={COMPONENT_LABELS} />
        </div>
      </div>

      <p className="text-2xs text-text-disabled">
        共 {metrics.annotation_total} 条批注 · 经验卡 {metrics.experience_cards.active} 激活 /
        {" "}{metrics.experience_cards.candidate} 候选 · 指标随每次审核裁决自动更新
      </p>
    </div>
  );
}
