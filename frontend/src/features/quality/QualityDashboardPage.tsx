import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { Gauge, Loader2 } from "lucide-react";

/** v9.7 §27 quality dashboard — first-pass yield WITH sample (78% (18/23)), etc. */
export function QualityDashboardPage({ bookId }: { bookId: string }) {
  const [trends, setTrends] = useState<any>(null);
  const [overview, setOverview] = useState<any>(null);
  const [roots, setRoots] = useState<any>(null);
  const [agents, setAgents] = useState<any>(null);
  const [aiTone, setAiTone] = useState<any>(null);
  const [techEff, setTechEff] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [t, o, r, a, at, te] = await Promise.all([
        api.quality.trends(bookId),
        api.quality.overview(bookId),
        api.quality.rootCauses(bookId),
        api.quality.agentPerformance(bookId),
        api.quality.aiToneSummary(bookId),
        api.quality.techniqueEffectiveness(bookId),
      ]);
      setTrends(t);
      setOverview(o);
      setRoots(r);
      setAgents(a);
      setAiTone(at);
      setTechEff(te);
    } catch (e: any) {
      setErr(e?.message || String(e));
    }
  }, [bookId]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 60000);
    return () => clearInterval(timer);
  }, [load]);

  if (err) return <div className="p-6 text-xs text-red-400">{err}</div>;

  const firstPass = trends?.first_pass_yield;
  const reviewed = trends?.total_reviewed || 0;
  const kpis = [
    { label: "首轮良品率", value: firstPass != null ? `${firstPass}%（${trends.first_pass_accepted}/${reviewed}）` : "—" },
    { label: "审核覆盖率", value: overview?.sample ? `${overview.sample} 条质量信号` : "—" },
    { label: "AI-Tone 确认率", value: aiTone?.confirmed_rate != null ? `${(aiTone.confirmed_rate * 100).toFixed(0)}%` : "—" },
    { label: "技法卡有效性", value: techEff?.pending != null ? `${techEff.effective} / 有效${techEff.ineffective}无效` : "—" },
  ];

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Gauge size={16} className="text-brand-accent" />
        <h1 className="text-sm text-text-primary">质量仪表盘</h1>
        <span className="text-2xs text-text-tertiary">人工审核 · AI-Audit · AI-Tone · 技法 · 模型</span>
        {!trends && <Loader2 size={13} className="animate-spin text-text-tertiary" />}
      </div>

      {trends && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {kpis.map((k) => (
            <div key={k.label} className="panel-elevated rounded-lg px-4 py-3">
              <div className="text-2xs text-text-disabled">{k.label}</div>
              <div className="text-sm font-mono text-text-primary mt-0.5">{k.value}</div>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <section className="panel p-4">
          <div className="text-2xs text-text-disabled mb-2">常见缺陷根因（人工归因）</div>
          {roots?.root_causes && Object.keys(roots.root_causes).length ? (
            <div className="space-y-1.5">
              {Object.entries(roots.root_causes as Record<string, number>).slice(0, 8).map(([label, v]) => (
                <div key={label} className="flex items-center gap-2 text-xs">
                  <span className="text-text-secondary w-40 truncate">{label}</span>
                  <div className="flex-1 h-1.5 bg-bg-surface rounded-full overflow-hidden">
                    <div className="h-full bg-warning/70 rounded-full" style={{ width: `${Math.min(100, (v || 0) * 100)}%` }} />
                  </div>
                  <span className="font-mono text-text-primary w-12 text-right">{v}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-2xs text-text-tertiary">暂无根因归因数据</div>
          )}
        </section>

        <section className="panel p-4">
          <div className="text-2xs text-text-disabled mb-2">各 Agent 关键指标</div>
          <div className="space-y-2">
            {agents?.agents && Object.keys(agents.agents).length ? (
              Object.entries(agents.agents as Record<string, any>).map(([role, metrics]) => (
                <div key={role} className="text-xs">
                  <div className="text-text-primary">{role}</div>
                  <div className="flex flex-wrap gap-2 mt-1">
                    {Object.entries(metrics as Record<string, any>).slice(0, 5).map(([k, v]) => (
                      <span key={k} className="border border-border-subtle rounded px-1.5 py-0.5 text-2xs text-text-secondary">
                        {k}: {v ?? "—"}
                      </span>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <div className="text-2xs text-text-tertiary">暂无按 Agent 的质量信号（人工审核后产生）</div>
            )}
          </div>
        </section>
      </div>

      {trends && (
        <section className="panel p-4">
          <div className="text-2xs text-text-disabled mb-2">状态分布</div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(trends.status_distribution || {}).map(([st, cnt]: any) => (
              <span key={st} className="border border-border rounded px-2 py-1 text-2xs text-text-secondary">
                {st}: {cnt}
              </span>
            ))}
          </div>
          {trends.score_trend && trends.score_trend.length > 0 && (
            <div className="mt-3">
              <div className="text-2xs text-text-disabled mb-1">最近评分走势</div>
              <div className="flex items-end gap-1 h-16">
                {trends.score_trend.slice(-20).map((t: any, i: number) => (
                  <div key={i} className="flex-1 flex flex-col items-center gap-0.5" title={`第${t.chapter_no}章 ${t.score}`}>
                    <div
                      className={`w-full rounded-t ${t.grade === "A" ? "bg-success/70" : t.grade === "B" ? "bg-info/70" : t.grade === "C" ? "bg-warning/70" : "bg-danger/70"}`}
                      style={{ height: `${Math.max(4, (t.score || 0) * 0.16)}px` }}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
