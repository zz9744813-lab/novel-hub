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
  role_evidence: "核对角色能力证据",
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
  const [evidence, setEvidence] = useState<any>(null);
  const [activeRun, setActiveRun] = useState<any>(null);
  const [activeEval, setActiveEval] = useState<any>(null);
  const [evalSubmitting, setEvalSubmitting] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);
  const evalPollRef = useRef<number | null>(null);

  const loadStatic = useCallback(async () => {
    try {
      const [c, p, e] = await Promise.all([
        api.modelSetup.current(),
        api.modelSetup.performance("24h"),
        api.modelSetup.evidence(),
      ]);
      setCurrent(c);
      setPerf(p);
      setEvidence(e);
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

  const pollEval = useCallback(async (runId: string, catalogId: string, modelId: string) => {
    try {
      const r = await api.modelSetup.evalRun(runId);
      const deduplicatedSource = r.status === "deduplicated" && r.result_summary?.source_run_id;
      if (deduplicatedSource && deduplicatedSource !== runId) {
        setActiveEval({ ...r, run_id: deduplicatedSource, catalog_id: catalogId, model_id: modelId });
        evalPollRef.current = window.setTimeout(
          () => pollEval(deduplicatedSource, catalogId, modelId),
          1000,
        );
        return;
      }
      const next = { ...r, run_id: runId, catalog_id: catalogId, model_id: modelId };
      setActiveEval(next);
      if (r.status === "queued" || r.status === "running" || r.status === "in_progress") {
        evalPollRef.current = window.setTimeout(() => pollEval(runId, catalogId, modelId), 2000);
      } else {
        if (evalPollRef.current) window.clearTimeout(evalPollRef.current);
        await loadStatic();
      }
    } catch (e: any) {
      if (evalPollRef.current) window.clearTimeout(evalPollRef.current);
      setErr(e?.message || String(e));
    }
  }, [loadStatic]);

  useEffect(() => {
    loadStatic();
    return () => {
      if (pollRef.current) window.clearTimeout(pollRef.current);
      if (evalPollRef.current) window.clearTimeout(evalPollRef.current);
    };
  }, [loadStatic]);

  const submitEvaluation = async (
    model: any,
    mode: "qualification" | "context_ladder",
    force = false,
  ) => {
    const action = mode === "qualification" ? "能力资格评测" : "上下文阶梯认证";
    if (force && !window.confirm(`强制重跑 ${model.model_id} 的${action}？这会绕过已有证据并产生完整模型调用。`)) return;
    setEvalSubmitting(`${model.id}:${mode}`);
    setErr(null);
    try {
      const r = mode === "qualification"
        ? await api.modelSetup.qualify(model.id, force)
        : await api.modelSetup.contextCertify(model.id, force);
      const runId = r.run_id || r.id;
      const next = { ...r, mode, run_id: runId, catalog_id: model.id, model_id: model.model_id };
      setActiveEval(next);
      if (runId && (r.queued || r.status === "queued" || r.status === "running" || r.status === "in_progress")) {
        pollEval(runId, model.id, model.model_id);
      } else {
        await loadStatic();
      }
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setEvalSubmitting(null);
    }
  };

  const cancelEvaluation = async () => {
    if (!activeEval?.run_id) return;
    try {
      await api.modelSetup.cancelEvalRun(activeEval.run_id);
      await pollEval(activeEval.run_id, activeEval.catalog_id, activeEval.model_id);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  };

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
  const evidenceRows = evidence?.items || [];
  const evalIsRunning = ["queued", "running", "in_progress"].includes(activeEval?.status);
    
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

      {/* ②-b 能力证据（一次性）vs 连接健康（持续）—— 分开展示 */}
      <section className="panel p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-2xs text-text-disabled">模型能力证据（一次性资格）</span>
          <span className="text-2xs text-text-tertiary">模型身份、题库或评测器版本不变时直接复用</span>
        </div>
        {activeEval && (
          <div className="rounded-md border border-border bg-bg-base px-3 py-2 text-xs flex flex-wrap items-center justify-between gap-2">
            <div className="text-text-secondary">
              <span className="text-text-primary">{activeEval.model_id}</span>
              {" · "}{activeEval.mode === "context_ladder" ? "上下文认证" : "能力资格"}
              {" · "}<span className={evalIsRunning ? "text-brand-accent" : activeEval.status === "succeeded" ? "text-green-400" : "text-amber-400"}>
                {evaluationStatusLabel(activeEval.status)}
              </span>
              {activeEval.gateway_calls != null ? ` · ${activeEval.gateway_calls} 次模型调用` : ""}
              {activeEval.reuse_reason === "cache_hit" ? " · 已复用既有证据" : ""}
            </div>
            {evalIsRunning && (
              <button onClick={cancelEvaluation} className="text-2xs text-amber-400">取消任务</button>
            )}
          </div>
        )}
        <div className="overflow-auto max-h-80 rounded-md border border-border">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-bg-elevated">
              <tr className="text-2xs text-text-tertiary text-left">
                <th className="px-2 py-1.5">Model</th>
                <th className="px-2 py-1.5">能力证据</th>
                <th className="px-2 py-1.5">角色门禁</th>
                <th className="px-2 py-1.5">上下文证据</th>
                <th className="px-2 py-1.5">证据 key / 版本</th>
                <th className="px-2 py-1.5">完成时间</th>
                <th className="px-2 py-1.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {evidenceRows.map((m: any) => {
                const roles = Object.values(m.role_evidence || {}) as any[];
                const passedRoles = roles.filter((role) => role.state === "valid" && role.passed).length;
                const effectiveContext = m.context_profile?.effective;
                const rowSubmitting = evalSubmitting?.startsWith(`${m.id}:`);
                return (
                  <tr key={m.id} className="border-t border-border-subtle hover:bg-bg-hover/40">
                    <td className="px-2 py-1.5 text-text-primary">
                      <div>{m.model_id}</div>
                      <div className="text-2xs text-text-disabled">{m.provider} · {m.model_kind || "unknown"}</div>
                    </td>
                    <td className="px-2 py-1.5" title={m.ability?.reason || ""}>
                      <span className={evidenceStateClass(m.ability?.state)}>{evidenceStateLabel(m.ability?.state)}</span>
                      {!!m.ability?.changed_fields?.length && (
                        <div className="text-2xs text-amber-400">变更：{m.ability.changed_fields.join(", ")}</div>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-text-secondary" title={roleEvidenceTitle(m.role_evidence)}>
                      {m.text_generation_eligible ? `${passedRoles} / ${roles.length || 5} 通过` : "—"}
                    </td>
                    <td className="px-2 py-1.5" title={m.context?.reason || ""}>
                      <span className={evidenceStateClass(m.context?.state)}>{evidenceStateLabel(m.context?.state)}</span>
                      <div className="text-2xs text-text-tertiary">有效 {formatContextWindow(effectiveContext)}</div>
                    </td>
                    <td className="px-2 py-1.5 font-mono text-2xs text-text-tertiary">
                      <div>A {shortKey(m.ability_evaluation_key)} · {m.ability_evaluator_revision || "—"}</div>
                      <div>C {shortKey(m.context_evaluation_key)} · {m.context_evaluator_revision || "—"}</div>
                    </td>
                    <td className="px-2 py-1.5 text-2xs text-text-tertiary">
                      <div>A {formatDateTime(m.ability_completed_at)}</div>
                      <div>C {formatDateTime(m.context_completed_at)}</div>
                    </td>
                    <td className="px-2 py-1.5 min-w-48">
                      {m.text_generation_eligible ? (
                        <div className="flex flex-wrap gap-x-2 gap-y-1">
                          <button disabled={!!rowSubmitting || evalIsRunning} onClick={() => submitEvaluation(m, "qualification", false)} className="text-2xs text-brand-accent disabled:opacity-40">
                            {m.ability?.state === "valid" ? "复用能力" : "能力评测"}
                          </button>
                          <button disabled={!!rowSubmitting || evalIsRunning} onClick={() => submitEvaluation(m, "context_ladder", false)} className="text-2xs text-brand-accent disabled:opacity-40">
                            {m.context?.state === "valid" ? "复用上下文" : "上下文认证"}
                          </button>
                          <button disabled={!!rowSubmitting || evalIsRunning} onClick={() => submitEvaluation(m, "qualification", true)} className="text-2xs text-amber-400 disabled:opacity-40">强测能力</button>
                          <button disabled={!!rowSubmitting || evalIsRunning} onClick={() => submitEvaluation(m, "context_ladder", true)} className="text-2xs text-amber-400 disabled:opacity-40">强测上下文</button>
                        </div>
                      ) : (
                        <span className="text-2xs text-text-disabled">非文本 · 不进入写作评测</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {!evidenceRows.length && (
                <tr><td colSpan={7} className="px-2 py-4 text-center text-text-tertiary">暂无模型证据；先同步模型目录</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* ③ 连接健康（持续探针）—— 与能力证据分离 */}
      <section className="panel p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-2xs text-text-disabled">连接健康（持续 L1 探针 / 延迟 / 错误）</span>
          <span className="text-2xs text-text-tertiary">不影响能力证据有效期</span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Kpi label="可用模型" value={perf?.global ? `${perf.global.available_models} / ${perf.global.total_models}` : "—"} />
          <Kpi
            label="24h成功率"
            value={perf?.global?.overall_success_rate != null ? `${(perf.global.overall_success_rate * 100).toFixed(1)}%` : "—"}
          />
          <Kpi label="TTFT P50" value={perf?.global?.ttft_p50_global_ms ? `${(perf.global.ttft_p50_global_ms / 1000).toFixed(1)}s` : "—"} />
          <Kpi label="TTFT P95" value={perf?.global?.ttft_p95_global_ms ? `${(perf.global.ttft_p95_global_ms / 1000).toFixed(1)}s` : "—"} />
          <Kpi label="输出速度 P50" value={perf?.global?.tokens_per_second_p50_global != null ? `${perf.global.tokens_per_second_p50_global} tok/s` : "—"} />
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

function evaluationStatusLabel(status?: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "执行中",
    in_progress: "执行中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
    deduplicated: "已由同一任务处理",
  };
  return labels[status || ""] || status || "未知";
}

function evidenceStateLabel(state?: string) {
  const labels: Record<string, string> = {
    valid: "有效",
    stale: "已失效",
    missing: "未评测",
    excluded: "已排除",
  };
  return labels[state || ""] || state || "未知";
}

function evidenceStateClass(state?: string) {
  if (state === "valid") return "text-green-400";
  if (state === "stale") return "text-amber-400";
  if (state === "excluded") return "text-text-disabled";
  return "text-text-secondary";
}

function formatContextWindow(value?: number | null) {
  if (!value) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  return `${Math.round(value / 1000)}K`;
}

function shortKey(value?: string | null) {
  return value ? `${value.slice(0, 8)}…` : "—";
}

function formatDateTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

function roleEvidenceTitle(value?: Record<string, any>) {
  const rows = Object.entries(value || {}).map(([role, item]) => {
    const status = item?.state === "valid" && item?.passed ? "通过" : evidenceStateLabel(item?.state);
    const score = item?.score == null ? "—" : Number(item.score).toFixed(1);
    return `${ROLE_LABELS[role] || role}: ${status} (${score})`;
  });
  return rows.join("\n");
}
