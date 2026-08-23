import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import type {
  EditorialExperimentItem,
  EditorialParetoCandidate,
  EditorialProposalItem,
} from "../../api";
import {
  Loader2,
  RefreshCw,
  FlaskConical,
  Check,
  X,
  Rocket,
  Undo2,
  AlertTriangle,
  Sparkles,
} from "lucide-react";

const PROPOSAL_STATUS: Record<string, { label: string; cls: string }> = {
  proposed: { label: "待审批", cls: "bg-warning/10 border-warning/30 text-warning" },
  approved: { label: "已批准", cls: "bg-brand-accent/10 border-brand-accent/30 text-brand-accent" },
  experimenting: { label: "实验中", cls: "bg-cyan-400/10 border-cyan-400/30 text-cyan-300" },
  promoted: { label: "已上线", cls: "bg-success/10 border-success/30 text-success" },
  rolled_back: { label: "已回滚", cls: "bg-white/5 border-white/15 text-text-tertiary" },
  rejected: { label: "已否决", cls: "bg-danger/10 border-danger/30 text-danger" },
};

const RISK_LABELS: Record<string, string> = { low: "低风险", medium: "中风险", high: "高风险" };

const CANDIDATE_SOURCE: Record<string, string> = {
  baseline: "基线",
  experience_card: "经验卡",
  proposal_patch: "提案补丁",
};

export function ImprovementPanel({ bookId }: { bookId: string }) {
  const [proposals, setProposals] = useState<EditorialProposalItem[]>([]);
  const [experiments, setExperiments] = useState<EditorialExperimentItem[]>([]);
  const [pareto, setPareto] = useState<EditorialParetoCandidate[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setRefreshing(true);
      try {
        const [ps, es] = await Promise.all([
          api.editorial.proposals(bookId),
          api.editorial.experiments(bookId),
        ]);
        setProposals(ps);
        setExperiments(es);
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

  const act = async (id: string, fn: () => Promise<unknown>, okMsg: string) => {
    setBusyId(id);
    try {
      await fn();
      setNotice(okMsg);
      await load(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const runGepa = async (id: string) => {
    setBusyId(id);
    try {
      const res = await api.editorial.createExperiment(bookId, id, true);
      setPareto(res.pareto_candidates || []);
      setNotice(
        `GEPA 搜索完成：${res.pareto_candidates?.length ?? 0} 个候选，建议 ${
          res.recommendation === "promote" ? "上线" : "继续观察"
        }`
      );
      await load(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-text-tertiary text-xs gap-2">
        <Loader2 size={16} className="animate-spin" /> 加载改进提案…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm text-text-secondary font-medium flex items-center gap-2">
          <FlaskConical size={14} className="text-brand-accent" />
          系统改进（提案 → 实验 → 上线）
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

      {error && (
        <div className="panel rounded-lg px-4 py-3 text-xs text-danger flex items-start gap-2">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span className="break-all">{error}</span>
        </div>
      )}
      {notice && (
        <div className="panel rounded-lg px-4 py-3 text-xs text-success">{notice}</div>
      )}

      <p className="text-2xs text-text-tertiary leading-5">
        提案由 L5 修订或审核反馈自动产生，人工审批后进入回放实验；硬门禁（禁用模式 / 经验卡遵循 / 非空输出）全部通过方可上线，上线后可一键回滚。
      </p>

      {/* proposals */}
      {proposals.length === 0 ? (
        <div className="panel rounded-card py-12 text-center space-y-2">
          <FlaskConical size={20} className="mx-auto text-text-disabled" />
          <p className="text-xs text-text-tertiary">暂无改进提案</p>
          <p className="text-2xs text-text-disabled">
            在审核工作台触发 L5 修订，或积累足够批注后自动产生
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {proposals.map((p) => {
            const meta = PROPOSAL_STATUS[p.status] ?? {
              label: p.status,
              cls: "bg-white/5 border-white/15 text-text-tertiary",
            };
            const patch = p.candidate_patch || {};
            const count = typeof patch.instruction_count === "number" ? patch.instruction_count : null;
            return (
              <div key={p.id} className="panel rounded-card p-4 space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`badge border ${meta.cls}`}>{meta.label}</span>
                    <span className="text-2xs text-text-tertiary">
                      {RISK_LABELS[p.risk_level] ?? p.risk_level} · {p.target_component}
                    </span>
                  </div>
                  {count != null && (
                    <span className="text-2xs text-text-disabled">依据 {count} 条批注</span>
                  )}
                </div>
                {p.reason && <p className="text-xs text-text-secondary leading-5">{p.reason}</p>}
                {p.effective_from_chapter != null && (
                  <p className="text-2xs text-text-disabled">生效章节：第 {p.effective_from_chapter} 章起</p>
                )}

                <div className="flex flex-wrap gap-1.5 pt-1 border-t border-white/5">
                  {p.status === "proposed" && (
                    <>
                      <button
                        className="btn px-2.5 py-1 text-2xs border-success/40 text-success hover:bg-success/10 disabled:opacity-50"
                        disabled={busyId === p.id}
                        onClick={() => act(p.id, () => api.editorial.reviewProposal(p.id, true), "提案已批准")}
                      >
                        <Check size={11} /> 批准
                      </button>
                      <button
                        className="btn px-2.5 py-1 text-2xs hover:text-danger disabled:opacity-50"
                        disabled={busyId === p.id}
                        onClick={() => act(p.id, () => api.editorial.reviewProposal(p.id, false), "提案已否决")}
                      >
                        <X size={11} /> 否决
                      </button>
                    </>
                  )}
                  {(p.status === "approved" || p.status === "experimenting") && (
                    <>
                      <button
                        className="btn px-2.5 py-1 text-2xs disabled:opacity-50"
                        disabled={busyId === p.id}
                        onClick={() =>
                          act(p.id, () => api.editorial.createExperiment(bookId, p.id), "回放实验已完成")
                        }
                      >
                        <FlaskConical size={11} /> 运行回放实验
                      </button>
                      <button
                        className="btn px-2.5 py-1 text-2xs border-cyan-400/40 text-cyan-300 hover:bg-cyan-400/10 disabled:opacity-50"
                        disabled={busyId === p.id}
                        onClick={() => runGepa(p.id)}
                      >
                        <Sparkles size={11} /> GEPA 搜索
                      </button>
                    </>
                  )}
                  {p.status === "approved" && (
                    <button
                      className="btn px-2.5 py-1 text-2xs border-brand-accent/40 text-brand-accent hover:bg-brand-accent/10 disabled:opacity-50"
                      disabled={busyId === p.id}
                      onClick={() => act(p.id, () => api.editorial.promoteProposal(p.id), "提案已上线")}
                    >
                      <Rocket size={11} /> 直接上线
                    </button>
                  )}
                  {p.status === "promoted" && (
                    <button
                      className="btn px-2.5 py-1 text-2xs hover:text-warning disabled:opacity-50"
                      disabled={busyId === p.id}
                      onClick={() => act(p.id, () => api.editorial.rollbackProposal(p.id), "已回滚")}
                    >
                      <Undo2 size={11} /> 回滚
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* experiments */}
      {experiments.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs text-text-secondary" style={{ fontWeight: 560 }}>
            回放实验历史
          </h3>
          {experiments.map((e) => (
            <div key={e.id} className="panel rounded-card p-3.5 space-y-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span
                  className={`badge border ${
                    e.recommendation === "promote"
                      ? "bg-success/10 border-success/30 text-success"
                      : "bg-warning/10 border-warning/30 text-warning"
                  }`}
                >
                  {e.recommendation === "promote" ? "建议上线" : "继续观察"}
                </span>
                <span className="text-2xs text-text-tertiary">{e.case_count} 个回归用例</span>
                {e.hard_pass != null && (
                  <span className={`text-2xs ${e.hard_pass ? "text-success" : "text-danger"}`}>
                    硬门禁{e.hard_pass ? "通过" : "未通过"}
                  </span>
                )}
                {e.started_at && (
                  <span className="text-2xs text-text-disabled ml-auto">
                    {new Date(e.started_at).toLocaleString()}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-4 text-2xs text-text-disabled">
                <span>
                  基线通过 {String((e.metrics_baseline as Record<string, unknown>)?.passed ?? "-")}/
                  {String((e.metrics_baseline as Record<string, unknown>)?.total ?? "-")}
                </span>
                <span>
                  候选通过 {String((e.metrics_candidate as Record<string, unknown>)?.passed ?? "-")}/
                  {String((e.metrics_candidate as Record<string, unknown>)?.total ?? "-")}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* GEPA-lite pareto candidates */}
      {pareto && pareto.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs text-text-secondary flex items-center gap-1.5" style={{ fontWeight: 560 }}>
            <Sparkles size={12} className="text-cyan-300" />
            GEPA 候选排名（通过率 × 文本保留率）
          </h3>
          <div className="panel rounded-card divide-y divide-white/5">
            {pareto.map((c, i) => (
              <div key={c.name} className="flex items-center gap-3 px-4 py-2.5">
                <span className="text-2xs text-text-disabled w-4">{i + 1}</span>
                <span className="text-2xs text-text-secondary flex-1 truncate">
                  {CANDIDATE_SOURCE[c.source] ?? c.source}
                </span>
                <span className="text-2xs text-text-tertiary">
                  通过率 {c.pass_rate != null ? `${c.pass_rate}%` : "-"}
                </span>
                <span className="text-2xs text-text-tertiary">
                  保留率 {Math.round(c.retention * 100)}%
                </span>
                <span className="text-2xs text-text-disabled">改动 {c.changed} 例</span>
                <span
                  className={`badge border ${
                    c.pareto_rank === 0
                      ? "bg-success/10 border-success/30 text-success"
                      : "bg-white/5 border-white/15 text-text-tertiary"
                  }`}
                >
                  {c.pareto_rank === 0 ? "前沿" : "被支配"}
                </span>
              </div>
            ))}
          </div>
          <p className="text-2xs text-text-disabled leading-5">
            非支配前沿（前沿）中的候选在通过率与保留率之间不存在全面更优的替代方案；实验自动采用前沿中通过率最高的候选。
          </p>
        </div>
      )}
    </div>
  );
}
