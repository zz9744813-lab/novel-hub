import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { Activity, Cpu, Gauge, Loader2, RefreshCw, ShieldCheck } from "lucide-react";

type Tab = "overview" | "models" | "routing" | "timeline" | "scores" | "policies";

const HEALTH_COLOR: Record<string, string> = {
  healthy: "text-emerald-400",
  degraded: "text-amber-400",
  unstable: "text-orange-400",
  unavailable: "text-red-400",
  unknown: "text-text-disabled",
};

const HEALTH_DOT: Record<string, string> = {
  healthy: "bg-emerald-400",
  degraded: "bg-amber-400",
  unstable: "bg-orange-400",
  unavailable: "bg-red-400",
  unknown: "bg-text-disabled",
};

export function ModelCenterPage() {
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<any>(null);
  const [models, setModels] = useState<any[] | null>(null);
  const [routing, setRouting] = useState<any[]>([]);
  const [timeline, setTimeline] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const [o, m, r, t] = await Promise.all([
        api.modelCenter.overview(),
        api.modelCenter.models(),
        api.modelCenter.routing(),
        api.modelCenter.timeline(),
      ]);
      setOverview(o);
      setModels(m.items);
      setRouting(r.items);
      setTimeline(t.items);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 30000);
    return () => clearInterval(timer);
  }, [refresh]);

  const handleSync = async () => {
    setBusy(true);
    try {
      await api.modelCenter.sync();
      await refresh();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleProbeAll = async () => {
    setBusy(true);
    try {
      await api.modelCenter.probeAll();
      await refresh();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu size={16} className="text-brand-accent" />
          <h1 className="text-sm text-text-primary">模型中心</h1>
          <span className="text-2xs text-text-tertiary">Model Autopilot</span>
        </div>
        <div className="flex gap-2">
          {busy && <Loader2 size={14} className="animate-spin text-text-tertiary" />}
          <button onClick={handleSync} className="btn text-xs py-1.5 px-3 flex items-center gap-1">
            <RefreshCw size={12} /> 同步目录
          </button>
          <button onClick={handleProbeAll} className="btn text-xs py-1.5 px-3 flex items-center gap-1">
            <Activity size={12} /> 全部探活
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-1">
        {(
          [
            ["overview", "总览"],
            ["models", "模型状态"],
            ["routing", "Agent 自动分配"],
            ["timeline", "路由记录"],
            ["scores", "能力评分"],
            ["policies", "路由策略"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`text-xs px-3 py-1.5 rounded-md ${
              tab === key ? "bg-brand/15 text-brand-accent" : "text-text-secondary hover:bg-bg-hover"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {err && <div className="text-xs text-red-400">{err}</div>}

      {tab === "overview" && <OverviewPanel data={overview} />}
      {tab === "models" && <ModelsTable models={models} onChanged={refresh} />}
      {tab === "routing" && <RoutingPanel routes={routing} models={models || []} />}
      {tab === "timeline" && <TimelinePanel events={timeline} />}
      {tab === "scores" && <ScorePanel models={models || []} />}
      {tab === "policies" && <PolicyPanel />}
    </div>
  );
}

function OverviewPanel({ data }: { data: any }) {
  if (!data) {
    return <div className="p-6 text-xs text-text-tertiary">加载总览…</div>;
  }
  const cards = [
    { label: "Providers", value: data.providers },
    { label: "Models", value: data.models },
    { label: "Healthy", value: data.healthy, cls: "text-emerald-400" },
    { label: "Degraded", value: data.degraded, cls: "text-amber-400" },
    { label: "Unavailable", value: data.unavailable, cls: "text-red-400" },
    { label: "Unknown", value: data.unknown, cls: "text-text-tertiary" },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
        {cards.map((c) => (
          <div key={c.label} className="panel p-3 text-center">
            <div className={`text-xl font-mono ${c.cls || "text-text-primary"}`}>{c.value}</div>
            <div className="text-2xs text-text-tertiary mt-1">{c.label}</div>
          </div>
        ))}
      </div>
      <div className="panel p-4">
        <div className="text-2xs text-text-disabled mb-2">警报</div>
        {(data.alerts || []).length ? (
          <ul className="space-y-1 text-xs text-text-secondary">
            {(data.alerts || []).slice(0, 10).map((a: any, i: number) => (
              <li key={i} className={a.level === "danger" ? "text-red-400" : "text-amber-400"}>
                {a.message}
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-xs text-text-tertiary">无警报</div>
        )}
      </div>
    </div>
  );
}

function ModelsTable({ models, onChanged }: { models: any[] | null; onChanged: () => void }) {
  if (!models) return null;
  return (
    <div className="panel overflow-auto max-h-[70vh]">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-bg-elevated">
          <tr className="text-2xs text-text-tertiary text-left">
            <th className="px-3 py-2">Provider</th>
            <th className="px-3 py-2">Model</th>
            <th className="px-3 py-2">Health</th>
            <th className="px-3 py-2">Context</th>
            <th className="px-3 py-2">15m</th>
            <th className="px-3 py-2">P95</th>
            <th className="px-3 py-2">Auto</th>
            <th className="px-3 py-2">操作</th>
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m.id} className="border-t border-border-subtle hover:bg-bg-hover/40">
              <td className="px-3 py-1.5 text-text-secondary">{m.provider}</td>
              <td className="px-3 py-1.5 text-text-primary">{m.model_id}</td>
              <td className="px-3 py-1.5">
                <span className={`inline-flex items-center gap-1.5 ${HEALTH_COLOR[m.health?.status] || "text-text-tertiary"}`}>
                  <span className={`w-1.5 h-1.5 rounded-full ${HEALTH_DOT[m.health?.status] || "bg-text-disabled"}`} />
                  {m.health?.status}
                </span>
              </td>
              <td className="px-3 py-1.5 font-mono text-text-secondary">
                {m.capability?.context_window ? `${(m.capability.context_window / 1000).toFixed(0)}K` : "—"}
              </td>
              <td className="px-3 py-1.5 font-mono text-text-secondary">
                {m.health?.success_rate_15m != null ? `${(m.health.success_rate_15m * 100).toFixed(1)}%` : "—"}
              </td>
              <td className="px-3 py-1.5 font-mono text-text-secondary">
                {m.health?.p95_latency_ms ? `${m.health.p95_latency_ms}ms` : "—"}
              </td>
              <td className="px-3 py-1.5">
                <input
                  type="checkbox"
                  checked={!!m.auto_route_enabled}
                  onChange={async (e) => {
                    if (e.target.checked) {
                      await api.modelCenter.enableAutoRoute(m.id);
                      onChanged();
                    }
                  }}
                  title="启用后可参与自动路由"
                />
              </td>
              <td className="px-3 py-1.5">
                <button
                  onClick={async () => {
                    await api.modelCenter.probeNow(m.id);
                    onChanged();
                  }}
                  className="btn text-2xs py-1 px-2"
                >
                  探活
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RoutingPanel({ routes, models }: { routes: any[]; models: any[] }) {
  if (!routes.length) {
    return <div className="panel p-4 text-xs text-text-tertiary">暂无绑定/路由。请在模型绑定面板配置。</div>;
  }
  return (
    <div className="space-y-2">
      {routes.map((r) => (
        <div key={r.agent_role} className="panel p-3 flex flex-wrap items-center gap-4 text-xs">
          <div className="w-32 text-text-primary">{r.agent_role}</div>
          <div className="w-16 text-2xs text-text-tertiary">
            {r.mode === "auto" ? "AUTO" : r.mode === "hybrid" ? "HYBRID" : "MANUAL"}
          </div>
          <div className="flex items-center gap-2 text-text-secondary">
            <ShieldCheck size={12} className="text-brand-accent" />
            <span>{(r.primary?.model || "未配置") + (r.primary ? ` · ${r.primary.provider}` : "")}</span>
          </div>
          {(r.fallbacks || []).length > 0 && (
            <div className="text-text-tertiary">
              备用: {r.fallbacks.map((f: any) => f.model).join(" / ")}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function TimelinePanel({ events }: { events: any[] }) {
  if (!events.length) {
    return <div className="panel p-4 text-xs text-text-tertiary">暂无路由记录（真实调用后出现）。</div>;
  }
  return (
    <div className="panel p-3 space-y-1 text-xs font-mono">
      {events.map((e) => (
        <div key={e.id} className="flex gap-3 items-baseline">
          <span className="text-text-disabled">{e.created_at || ""}</span>
          <span className="text-text-secondary w-28">{e.agent_role || ""}</span>
          <span className="text-text-primary">{e.provider}/{e.model}</span>
          <span className={e.route_type === "fallback" ? "text-amber-400" : "text-text-tertiary"}>
            {e.route_type}
          </span>
        </div>
      ))}
    </div>
  );
}

function ScorePanel({ models }: { models: any[] }) {
  const roleNames: Record<string, string> = {
    chapter_planner: "ChapterPlanner",
    draft_writer: "DraftWriter",
    review_agent: "ReviewAgent",
    state_extractor: "StateExtractor",
    style_analyzer: "StyleAnalyzer",
  };
  return (
    <div className="space-y-3">
      {Object.keys(roleNames).map((role) => {
        const ranked = models
          .map((m) => ({ model: m.model_id, provider: m.provider, score: m.role_scores?.[role]?.composite_score }))
          .filter((x) => x.score != null)
          .sort((a, b) => (b.score || 0) - (a.score || 0))
          .slice(0, 6);
        return (
          <div key={role} className="panel p-3">
            <div className="text-xs text-text-primary mb-2">{roleNames[role]} 排名</div>
            {ranked.length ? (
              <div className="space-y-1">
                {ranked.map((x, i) => (
                  <div key={`${x.provider}-${x.model}`} className="flex items-center gap-3 text-xs">
                    <span className="w-5 text-2xs text-text-disabled">{i + 1}</span>
                    <span className="text-text-secondary w-48 truncate">{x.provider}/{x.model}</span>
                    <div className="flex-1 h-1.5 bg-bg-surface rounded-full overflow-hidden">
                      <div className="h-full bg-brand/70 rounded-full" style={{ width: `${x.score}%` }} />
                    </div>
                    <span className="font-mono text-text-primary w-8 text-right">{x.score}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-2xs text-text-tertiary">暂无评分（执行"重算评分"或等生产数据积累）</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function PolicyPanel() {
  const [policies, setPolicies] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [mode, setMode] = useState("hybrid");

  const load = useCallback(async () => {
    const r = await api.modelCenter.policies();
    setPolicies(r.items || []);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const create = async () => {
    if (!name.trim()) return;
    await api.modelCenter.createPolicy({ name: name.trim(), mode });
    setName("");
    load();
  };

  return (
    <div className="space-y-3">
      <div className="panel p-3 flex flex-wrap items-center gap-2 text-xs">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="策略名称"
          className="rounded border border-border bg-bg-input px-2 py-1.5 text-xs text-text-primary placeholder:text-text-disabled outline-none"
        />
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          className="rounded border border-border bg-bg-input px-2 py-1.5 text-xs text-text-primary outline-none"
        >
          <option value="manual">manual</option>
          <option value="auto">auto</option>
          <option value="hybrid">hybrid</option>
        </select>
        <button onClick={create} className="btn-primary text-xs py-1.5 px-3">
          新建策略
        </button>
      </div>
      <div className="panel p-3 space-y-1">
        {policies.map((p) => (
          <div key={p.id} className="flex items-center justify-between text-xs">
            <span className="text-text-primary">{p.name}</span>
            <span className="text-text-tertiary">
              {p.mode} · fallback {p.fallback_count} · 质量下限 {p.min_quality_score}
            </span>
          </div>
        ))}
        {!policies.length && <div className="text-xs text-text-tertiary">暂无策略（默认 hybrid 策略生效）</div>}
      </div>
      <div className="flex gap-2">
        <Gauge size={13} className="text-text-tertiary" />
        <span className="text-2xs text-text-tertiary">
          默认评分权重: 质量 45% · 可靠性 25% · 上下文 20% · 健康 10%
        </span>
      </div>
    </div>
  );
}
