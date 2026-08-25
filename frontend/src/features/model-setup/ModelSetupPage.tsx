import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { Gauge, Loader2, RefreshCw, RotateCcw, Sparkles, Zap } from "lucide-react";
import { ModelBindingPanel } from "../../components/ModelBindingPanel";

const PHASE_LABELS: Record<string, string> = {
  queued: "排队",
  catalog_sync: "同步模型列表",
  provider_check: "Provider 连接",
  model_health: "基础健康",
  performance_probe: "性能测速",
  capability: "能力确认",
  role_benchmark: "角色能力评测",
  score: "智能评分",
  recommendation: "推荐配置",
  apply: "应用配置",
  verify: "验证",
  done: "完成",
};

const ROLE_LABELS: Record<string, string> = {
  chapter_planner: "ChapterPlanner",
  draft_writer: "DraftWriter",
  review_agent: "ReviewAgent",
  state_extractor: "StateExtractor",
  style_analyzer: "StyleAnalyzer",
};

export function ModelSetupPage() {
  const [current, setCurrent] = useState<any>(null);
  const [perf, setPerf] = useState<any>(null);
  const [activeRun, setActiveRun] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const loadStatic = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([
        api.modelSetup.current(),
        api.modelSetup.performance("24h"),
      ]);
      setCurrent(c);
      setPerf(p);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  }, []);

  const pollRun = useCallback(async (runId: string) => {
    try {
      const r = await api.modelSetup.run(runId);
      setActiveRun(r);
      if (r.status === "queued" || r.status === "running") {
        pollRef.current = window.setTimeout(() => pollRun(runId), 2500);
      } else {
        if (pollRef.current) window.clearTimeout(pollRef.current);
        await loadStatic();
        setActiveRun(null);
      }
    } catch {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    }
  }, [loadStatic]);

  useEffect(() => {
    loadStatic();
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
    };
  }, [loadStatic]);

  const start = async (kind: "detect" | "auto") => {
    setBusy(true);
    setErr(null);
    try {
      const r = kind === "detect" ? await api.modelSetup.detect() : await api.modelSetup.autoConfigure();
      setActiveRun(r);
      pollRun(r.id);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const last = current?.last_run;
  const rec = current?.recommendation || {};
  const perfRows = perf?.models || [];
  const healthCounts = perfRows.filter((m: any) => m.health_status === "healthy" || m.health_status === "degraded").length;
  const tpsValues = perfRows.map((m: any) => m.tokens_per_second_p50).filter((v: any) => v != null);

  return (
    <div className="space-y-4">
      {/* ① 操作区 */}
      <section className="panel-elevated rounded-xl p-5">
        <div className="flex items-center gap-2 mb-1">
          <Gauge size={16} className="text-brand-accent" />
          <h2 className="text-sm text-text-primary">模型配置</h2>
        </div>
        <p className="text-2xs text-text-tertiary mb-3">
          {last
            ? `上次自动检测：${last.finished_at ? new Date(last.finished_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "—"} · ${last.detected_models}模型 · ${last.healthy_models}可用`
            : "尚未运行自动检测"}
        </p>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => start("detect")} disabled={busy || !!activeRun} className="btn-primary text-xs py-2 px-4 flex items-center gap-1.5">
            {busy || activeRun ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            自动检测
          </button>
          <button onClick={() => start("auto")} disabled={busy || !!activeRun} className="btn text-xs py-2 px-4 flex items-center gap-1.5">
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />}
            ⚡ 一键智能配置
          </button>
        </div>

        {activeRun && (
          <div className="mt-4 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-text-primary flex items-center gap-2">
                <Loader2 size={13} className="animate-spin text-brand-accent" />
                {activeRun.status === "running" || activeRun.status === "queued"
                  ? `${PHASE_LABELS[activeRun.phase] || activeRun.phase} · ${activeRun.progress}%`
                  : activeRun.status === "succeeded" ? "✓ 完成" : "⚠ 失败"}
              </span>
              <span className="text-2xs text-text-tertiary">
                {activeRun.finished}/{activeRun.total} 模型{activeRun.current_model ? ` · ${activeRun.current_model}` : ""}
              </span>
            </div>
            <div className="h-1.5 bg-bg-surface rounded-full overflow-hidden">
              <div className="h-full bg-brand/70 rounded-full transition-all" style={{ width: `${activeRun.progress || 0}%` }} />
            </div>
            {activeRun.error_json && (
              <div className="text-xs text-red-400">{activeRun.error_json.code} · {JSON.stringify(activeRun.error_json.detail || "")}</div>
            )}
          </div>
        )}
      </section>

      {/* ② 当前自动配置 */}
      {!!Object.keys(rec).length && (
        <section className="panel p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-2xs text-text-disabled">当前自动配置</span>
            {last?.action === "detect_and_configure" && last?.status === "succeeded" && (
              <button
                onClick={async () => {
                  if (!window.confirm("撤销本次自动配置，恢复配置前状态？")) return;
                  await api.modelSetup.rollback(last.id);
                  await loadStatic();
                }}
                className="text-2xs text-amber-400 flex items-center gap-1"
              >
                <RotateCcw size={11} /> 撤销本次自动配置
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {Object.entries(rec).map(([role, assign]: any) => (
              <div key={role} className="rounded-md border border-border bg-bg-base p-3 text-xs">
                <div className="text-text-primary mb-1.5">{ROLE_LABELS[role] || role}</div>
                <div className="space-y-1">
                  <div className="flex items-center gap-2 text-text-secondary">
                    <span className="text-2xs text-text-disabled w-12 shrink-0">主</span>
                    <span>{assign.primary?.model || "—"}</span>
                    <span className="text-2xs text-text-tertiary font-mono">{assign.primary?.route_score || assign.primary?.scores?.role_quality_score || ""}</span>
                  </div>
                  {(assign.fallbacks || []).slice(0, 2).map((fb: any, i: number) => (
                    <div key={i} className="flex items-center gap-2 text-text-secondary">
                      <span className="text-2xs text-text-disabled w-12 shrink-0">备{i + 1}</span>
                      <span>{fb.model || "—"}</span>
                      <span className="text-2xs text-text-tertiary">{fb.provider}</span>
                    </div>
                  ))}
                  {!assign.primary?.model && <div className="text-2xs text-red-400">无可配置模型（质量/上下文不足）</div>}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ③ 性能总览 */}
      <section className="panel p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-2xs text-text-disabled">模型性能</span>
          <span className="text-2xs text-text-tertiary">24h 窗口</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Kpi label="模型可用" value={`${healthCounts} / ${perfRows.length}`} />
          <Kpi
            label="24h成功率"
            value={perfRows.length ? `${((perfRows.filter((m: any) => (m.success_rate ?? 0) > 0.95).length / perfRows.length) * 100).toFixed(0)}%` : "—"}
          />
          <Kpi label="TTFT P50" value={perfRows[0]?.ttft_p50_ms ? `${(perfRows[0].ttft_p50_ms / 1000).toFixed(1)}s` : "—"} />
          <Kpi label="TTFT P95" value={perfRows[0]?.ttft_p95_ms ? `${(perfRows[0].ttft_p95_ms / 1000).toFixed(1)}s` : "—"} />
          <Kpi label="输出速度 P50" value={tpsValues.length ? `${Math.max(...tpsValues)} tok/s` : "—"} />
        </div>
        <div className="overflow-auto max-h-80 rounded-md border border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-bg-elevated">
              <tr className="text-2xs text-text-tertiary text-left">
                <th className="px-2 py-1.5">Model</th>
                <th className="px-2 py-1.5">Health</th>
                <th className="px-2 py-1.5">TTFT P50</th>
                <th className="px-2 py-1.5">TTFT P95</th>
                <th className="px-2 py-1.5">最多 Tok/s</th>
                <th className="px-2 py-1.5">Context</th>
                <th className="px-2 py-1.5">Draft</th>
                <th className="px-2 py-1.5">Planner</th>
              </tr>
            </thead>
            <tbody>
              {perfRows.map((m: any) => (
                <tr key={m.id} className="border-t border-border-subtle hover:bg-bg-hover/40">
                  <td className="px-2 py-1.5 text-text-primary">{m.model_id}</td>
                  <td className="px-2 py-1.5 text-text-secondary">{m.health_status}</td>
                  <td className="px-2 py-1.5 font-mono text-text-secondary">{m.ttft_p50_ms ? `${(m.ttft_p50_ms / 1000).toFixed(1)}s` : "—"}</td>
                  <td className="px-2 py-1.5 font-mono text-text-secondary">{m.ttft_p95_ms ? `${(m.ttft_p95_ms / 1000).toFixed(1)}s` : "—"}</td>
                  <td className="px-2 py-1.5 font-mono text-text-secondary">—</td>
                  <td className="px-2 py-1.5 font-mono text-text-secondary">{m.context_window ? `${(m.context_window / 1000).toFixed(0)}K` : "—"}</td>
                  <td className="px-2 py-1.5 font-mono text-text-secondary">{m.role_scores?.draft_writer?.composite_score ?? "—"}</td>
                  <td className="px-2 py-1.5 font-mono text-text-secondary">{m.role_scores?.chapter_planner?.composite_score ?? "—"}</td>
                </tr>
              ))}
              {!perfRows.length && (
                <tr><td colSpan={8} className="px-2 py-4 text-center text-text-tertiary">暂无性能数据，先运行"自动检测"</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* 高级手动配置（折叠） */}
      <details className="panel p-4">
        <summary className="text-xs text-text-secondary cursor-pointer select-none">▶ 高级手动配置</summary>
        <div className="mt-3">
          <ModelBindingPanel />
        </div>
      </details>

      {err && <div className="text-xs text-red-400">{err}</div>}
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-bg-base px-3 py-2">
      <div className="text-2xs text-text-disabled">{label}</div>
      <div className="text-sm text-text-primary font-mono mt-0.5">{value}</div>
    </div>
  );
}
