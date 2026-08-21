import { useEffect, useState } from "react";
import { api, SceneContractItem, SceneSimulationResult } from "../api";
import {
  Loader2,
  ScrollText,
  ChevronDown,
  ChevronRight,
  Play,
  Eye,
  Brain,
  Heart,
  Crosshair,
  Zap,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";
import clsx from "clsx";

const STATUS_BADGE: Record<string, string> = {
  proposed: "text-warning bg-warning-muted",
  validated: "text-success bg-success-muted",
  realized: "text-brand-accent bg-brand-muted",
  finalized: "text-info bg-info-muted",
};

function short(id: string | null | undefined, n = 6) {
  if (!id) return "—";
  return id.length > n ? id.slice(0, n) : id;
}

function num(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined) return "—";
  return Number(v).toFixed(digits);
}

function VadBar({ label, v }: { label: string; v: number }) {
  const pct = ((v + 1) / 2) * 100;
  const positive = v >= 0;
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-2xs text-text-disabled w-3">{label}</span>
      <div className="relative w-16 h-1 rounded bg-bg-surface">
        <div className="absolute top-0 bottom-0 left-1/2 w-px bg-border-strong" />
        <div
          className="absolute top-0 bottom-0 rounded"
          style={{
            left: positive ? "50%" : `${pct}%`,
            width: `${Math.abs(v) * 50}%`,
            background: positive ? "#8b8eff" : "#d4a24e",
          }}
        />
      </div>
      <span className="text-2xs font-mono text-text-tertiary w-8">{num(v)}</span>
    </div>
  );
}

function ContractDetail({ contract }: { contract: any }) {
  if (!contract) {
    return <div className="text-2xs text-text-disabled px-1">契约数据不可用</div>;
  }
  const events = contract.provisional_events || [];
  const edges = contract.causal_edges || [];
  const perceptions = contract.perceptions || [];
  const beliefs = contract.belief_deltas || [];
  const appraisals = contract.appraisals || [];
  const affects = contract.affect_transitions || [];
  const intentions = contract.intentions || [];
  const effects = contract.expected_effects || [];
  const expressions = contract.expression_constraints || [];

  const Section = ({
    icon,
    title,
    count,
    children,
  }: {
    icon: any;
    title: string;
    count: number;
    children: React.ReactNode;
  }) => {
    if (count === 0) return null;
    return (
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5 text-2xs text-text-disabled uppercase tracking-wider">
          {icon}
          <span style={{ fontWeight: 510 }}>{title}</span>
          <span className="font-mono">{count}</span>
        </div>
        {children}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <Section icon={<Crosshair size={10} />} title="事件" count={events.length}>
        <div className="grid gap-1">
          {events.map((e: any, i: number) => (
            <div
              key={i}
              className="flex items-start gap-2 text-2xs bg-bg-canvas/60 border border-border-subtle rounded-md px-2.5 py-1.5"
            >
              <span className="font-mono text-brand-accent shrink-0">{short(e.event_key, 14)}</span>
              <span className="text-text-secondary">{e.action}</span>
              {e.event_type && (
                <span className="badge bg-bg-surface text-text-tertiary text-2xs shrink-0 ml-auto">
                  {e.event_type}
                </span>
              )}
              {(e.hard_effects?.length || 0) > 0 && (
                <span className="badge bg-brand-muted text-brand-accent text-2xs shrink-0">
                  硬效应 {e.hard_effects.length}
                </span>
              )}
            </div>
          ))}
        </div>
      </Section>

      <Section icon={<Zap size={10} />} title="因果边" count={edges.length}>
        <div className="grid gap-1">
          {edges.map((e: any, i: number) => (
            <div
              key={i}
              className="flex items-center gap-2 text-2xs bg-bg-canvas/60 border border-border-subtle rounded-md px-2.5 py-1.5"
            >
              <span className="font-mono text-text-tertiary shrink-0">
                {short(e.from_key || e.from, 10)} → {short(e.to_key || e.to, 10)}
              </span>
              <span
                className={clsx(
                  "badge text-2xs shrink-0",
                  e.mode === "hard" ? "bg-brand-muted text-brand-accent" : "bg-bg-surface text-text-disabled"
                )}
              >
                {e.mode === "hard" ? "硬" : "软"} · {e.relation}
              </span>
              {e.mechanism && (
                <span className="text-text-disabled truncate">{e.mechanism}</span>
              )}
            </div>
          ))}
        </div>
      </Section>

      <Section icon={<Eye size={10} />} title="感知" count={perceptions.length}>
        <div className="grid gap-1">
          {perceptions.map((p: any, i: number) => (
            <div key={i} className="text-2xs text-text-secondary font-mono px-2.5">
              {short(p.character_id)} · {p.channel} · {short(p.event_key, 10)}
              {p.detail ? <span className="text-text-disabled"> · {p.detail}</span> : null}
            </div>
          ))}
        </div>
      </Section>

      <Section icon={<Brain size={10} />} title="信念变化" count={beliefs.length}>
        <div className="grid gap-1">
          {beliefs.map((b: any, i: number) => (
            <div
              key={i}
              className="flex items-center gap-2 text-2xs bg-bg-canvas/60 border border-border-subtle rounded-md px-2.5 py-1.5"
            >
              <span className="font-mono text-text-tertiary shrink-0">{short(b.character_id)}</span>
              <span className="font-mono text-text-secondary truncate">{b.belief_key}</span>
              <span className="ml-auto font-mono shrink-0">
                <span className="text-text-disabled">{num(b.before)}</span>
                <span className="text-text-disabled mx-1">→</span>
                <span className={b.after >= 0 ? "text-success" : "text-danger"}>{num(b.after)}</span>
              </span>
            </div>
          ))}
        </div>
      </Section>

      <Section icon={<Crosshair size={10} />} title="认知评价" count={appraisals.length}>
        <div className="grid gap-1">
          {appraisals.map((a: any, i: number) => (
            <div
              key={i}
              className="text-2xs bg-bg-canvas/60 border border-border-subtle rounded-md px-2.5 py-1.5 space-y-1"
            >
              <div className="flex items-center gap-2 font-mono">
                <span className="text-text-tertiary">{short(a.character_id)}</span>
                {a.event_key && <span className="text-text-disabled">· {short(a.event_key, 10)}</span>}
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-text-disabled">
                <span>目标一致 {num(a.goal_congruence)}</span>
                <span>新颖 {num(a.novelty)}</span>
                <span>可控 {num(a.controllability)}</span>
                <span>确定 {num(a.certainty)}</span>
                <span>规范 {num(a.norm_compatibility)}</span>
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section icon={<Heart size={10} />} title="情绪迁移" count={affects.length}>
        <div className="grid gap-1">
          {affects.map((t: any, i: number) => (
            <div
              key={i}
              className="bg-bg-canvas/60 border border-border-subtle rounded-md px-2.5 py-1.5 space-y-1"
            >
              <div className="flex items-center gap-2">
                <span className="text-2xs font-mono text-text-tertiary">{short(t.character_id)}</span>
                {t.shock && t.shock !== "none" && (
                  <span className="badge bg-danger-muted text-danger text-2xs">{t.shock}</span>
                )}
                {(t.derived_emotions || []).map((em: string, j: number) => (
                  <span key={j} className="badge bg-warning-muted text-warning text-2xs">
                    {em}
                  </span>
                ))}
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                <VadBar label="V" v={t.to_vad?.valence ?? 0} />
                <VadBar label="A" v={t.to_vad?.arousal ?? 0} />
                <VadBar label="D" v={t.to_vad?.dominance ?? 0} />
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section icon={<Crosshair size={10} />} title="意图与归因" count={intentions.length}>
        <div className="grid gap-1">
          {intentions.map((it: any, i: number) => (
            <div
              key={i}
              className="flex items-center gap-2 text-2xs bg-bg-canvas/60 border border-border-subtle rounded-md px-2.5 py-1.5"
            >
              <span className="font-mono text-text-tertiary shrink-0">{short(it.character_id)}</span>
              <span className="text-text-secondary truncate">{it.action_intent}</span>
              <span
                className={clsx(
                  "badge text-2xs shrink-0 ml-auto",
                  it.attribution_status === "supported"
                    ? "bg-success-muted text-success"
                    : "bg-warning-muted text-warning"
                )}
              >
                {it.attribution_status === "supported" ? "已归因" : "待归因"} · {it.weight}
              </span>
            </div>
          ))}
        </div>
      </Section>

      <Section icon={<Zap size={10} />} title="预期状态效应" count={effects.length}>
        <div className="grid gap-1">
          {effects.map((e: any, i: number) => (
            <div
              key={i}
              className="flex items-center gap-2 text-2xs bg-bg-canvas/60 border border-border-subtle rounded-md px-2.5 py-1.5"
            >
              <span className="font-mono text-text-secondary truncate">{e.path}</span>
              <span className="font-mono text-text-tertiary shrink-0 ml-auto">
                = {typeof e.value === "number" ? num(e.value) : String(e.value ?? "—")}
              </span>
              <span
                className={clsx(
                  "badge text-2xs shrink-0",
                  e.mode === "hard" ? "bg-brand-muted text-brand-accent" : "bg-bg-surface text-text-disabled"
                )}
              >
                {e.mode === "hard" ? "硬" : "软"}
              </span>
            </div>
          ))}
        </div>
      </Section>

      <Section icon={<Eye size={10} />} title="表达约束" count={expressions.length}>
        <div className="grid gap-1">
          {expressions.map((x: any, i: number) => (
            <div key={i} className="text-2xs text-text-tertiary font-mono px-2.5">
              {short(x.character_id)} · 可见{x.visibility || "—"} · 语控
              {x.speech_control || "—"} · 趋近{x.approach_tendency || "—"}
            </div>
          ))}
        </div>
      </Section>

      {(contract.must_realize?.length || contract.must_not_assert?.length) > 0 && (
        <div className="space-y-1.5">
          <div className="text-2xs text-text-disabled uppercase tracking-wider" style={{ fontWeight: 510 }}>
            硬性约束
          </div>
          {contract.must_realize?.map((m: string, i: number) => (
            <div key={i} className="flex gap-1.5 text-2xs text-text-secondary">
              <CheckCircle2 size={10} className="text-success shrink-0 mt-0.5" />
              {m}
            </div>
          ))}
          {contract.must_not_assert?.map((m: string, i: number) => (
            <div key={i} className="flex gap-1.5 text-2xs text-text-secondary">
              <AlertTriangle size={10} className="text-danger shrink-0 mt-0.5" />
              {m}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ContractCard({ item, index }: { item: SceneContractItem; index: number }) {
  const [open, setOpen] = useState(false);
  const [sim, setSim] = useState<SceneSimulationResult | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [simErr, setSimErr] = useState<string | null>(null);

  const s = item.summary || {};
  const badge = STATUS_BADGE[item.status] || "text-text-tertiary bg-bg-surface";

  const simulate = async () => {
    setSimulating(true);
    setSimErr(null);
    try {
      setSim(await api.sceneContracts.simulate(item.id));
    } catch (e: any) {
      setSimErr(e?.message || String(e));
    } finally {
      setSimulating(false);
    }
  };

  return (
    <div
      className="panel overflow-hidden"
      style={{ animation: `slideUp 0.3s ease-out ${index * 70}ms both` }}
    >
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2.5 p-3 text-left hover:bg-bg-hover/50 transition-colors"
      >
        {open ? (
          <ChevronDown size={13} className="text-text-disabled shrink-0" />
        ) : (
          <ChevronRight size={13} className="text-text-disabled shrink-0" />
        )}
        <span className="w-7 h-6 rounded bg-brand-muted/60 border border-brand/20 flex items-center justify-center text-2xs font-bold text-brand-accent font-mono shrink-0">
          S{item.scene_no}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-primary truncate" style={{ fontWeight: 510 }}>
              {s.dramatic_goal || "（无戏剧目标）"}
            </span>
            <span className={clsx("badge text-2xs shrink-0", badge)}>{item.status}</span>
          </div>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            <span className="text-2xs text-text-disabled font-mono">
              {(s.event_count ?? 0)} 事件 · {(s.edge_count ?? 0)} 边 · {(s.belief_count ?? 0)} 信念 ·{" "}
              {(s.appraisal_count ?? 0)} 评价
            </span>
            {(s.hard_effect_count ?? 0) > 0 && (
              <span className="badge bg-brand-muted text-brand-accent text-2xs">
                <Zap size={9} /> {s.hard_effect_count} 硬效应
              </span>
            )}
          </div>
        </div>
      </button>

      {open && (
        <div className="px-3.5 pb-3.5 space-y-3 animate-fade-in border-t border-border-subtle pt-3">
          <ContractDetail contract={item.contract} />

          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={simulate}
              disabled={simulating}
              className="btn-ghost text-2xs py-1 px-2.5 rounded border border-border"
              title="用因果引擎重放该契约，检查前置条件与硬效应"
            >
              {simulating ? (
                <Loader2 size={11} className="animate-spin" />
              ) : (
                <Play size={11} />
              )}
              模拟执行
            </button>
            <span className="text-2xs text-text-disabled font-mono truncate">
              hash {item.contract_hash?.slice(0, 12) || "—"}
            </span>
          </div>

          {simErr && (
            <div className="text-2xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-2.5 py-1.5">
              {simErr}
            </div>
          )}

          {sim && (
            <div
              className={clsx(
                "rounded-md border px-3 py-2.5 space-y-1.5 animate-slide-up",
                sim.ok
                  ? "border-success/25 bg-success-muted/40"
                  : "border-danger/30 bg-danger-muted/40"
              )}
            >
              <div className="flex items-center gap-1.5">
                {sim.ok ? (
                  <CheckCircle2 size={12} className="text-success" />
                ) : (
                  <AlertTriangle size={12} className="text-danger" />
                )}
                <span className="text-2xs" style={{ fontWeight: 510 }}>
                  模拟{sim.ok ? "通过" : "发现违规"} · {sim.findings.length} 条发现
                </span>
              </div>
              {sim.findings.map((f, i) => (
                <div key={i} className="text-2xs text-text-secondary font-mono">
                  <span className={f.severity === "error" ? "text-danger" : "text-warning"}>
                    [{f.code}]
                  </span>{" "}
                  {f.message || f.detail || ""}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function SceneContractInspector({ chapterId }: { chapterId: string }) {
  const [contracts, setContracts] = useState<SceneContractItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setErr(null);
    api.sceneContracts
      .list(chapterId)
      .then((r) => setContracts(r.contracts || []))
      .catch((e) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false));
  }, [chapterId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 gap-2 text-text-tertiary text-xs">
        <Loader2 size={14} className="animate-spin" />
        正在读取场景契约…
      </div>
    );
  }
  if (err) {
    return (
      <div className="panel flex flex-col items-center py-12 text-text-tertiary">
        <ScrollText size={24} className="mb-2 opacity-25" />
        <p className="text-xs">{err}</p>
      </div>
    );
  }
  if (contracts.length === 0) {
    return (
      <div className="panel flex flex-col items-center py-12 text-text-tertiary">
        <ScrollText size={24} className="mb-2 opacity-25" />
        <p className="text-xs">本章没有场景契约</p>
        <p className="text-2xs text-text-disabled mt-1">
          规划阶段编译契约；定稿后状态锁定为 finalized
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {contracts.map((c, i) => (
        <ContractCard key={c.id} item={c} index={i} />
      ))}
    </div>
  );
}
