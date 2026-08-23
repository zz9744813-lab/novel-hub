import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import type { ResearchHealth, ResearchScrapeSource } from "../../api";
import {
  AlertTriangle,
  Ban,
  FlaskConical,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Wrench,
  Zap,
} from "lucide-react";
import clsx from "clsx";

const STATUS_META: Record<string, { label: string; cls: string; icon: any; dot: string }> = {
  verified: { label: "已验证", cls: "text-success", icon: ShieldCheck, dot: "#27a644" },
  degraded: { label: "降级", cls: "text-warning", icon: AlertTriangle, dot: "#d4a24e" },
  blocked: { label: "被拦截", cls: "text-danger", icon: Ban, dot: "#e05555" },
  broken: { label: "规则失效", cls: "text-danger", icon: Wrench, dot: "#e05555" },
  experimental: { label: "实验", cls: "text-text-tertiary", icon: FlaskConical, dot: "#7a808c" },
  disabled: { label: "已禁用", cls: "text-text-disabled", icon: Ban, dot: "#50545c" },
};

function HealthPill({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-bg-surface px-2.5 py-1 text-2xs text-text-secondary">
      <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
      {label} <span className="font-mono">{value}</span>
    </span>
  );
}

export function SourceDiagnosticsPanel({
  onProbeSource,
}: {
  onProbeSource?: (source: ResearchScrapeSource) => void;
}) {
  const [sources, setSources] = useState<ResearchScrapeSource[]>([]);
  const [health, setHealth] = useState<ResearchHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, h] = await Promise.all([
        api.researchScrape.sources(),
        api.researchScrape.health(),
      ]);
      setSources(s);
      setHealth(h);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 size={18} className="animate-spin text-text-disabled" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* health 摘要 */}
      {health && (
        <div className="flex flex-wrap items-center gap-2 rounded-card border border-border bg-bg-panel px-4 py-3">
          <span className="text-2xs font-medium text-text-primary">书源健康</span>
          <span className="text-2xs text-text-disabled">·</span>
          <HealthPill label="已验证" value={health.verified_sources} color="#27a644" />
          <HealthPill label="实验" value={health.experimental_sources} color="#7a808c" />
          <HealthPill label="降级" value={health.degraded_sources} color="#d4a24e" />
          <HealthPill label="被拦截" value={health.blocked_sources} color="#e05555" />
          <HealthPill label="规则失效" value={health.broken_sources} color="#e05555" />
          <button className="ml-auto btn-ghost rounded p-1.5" onClick={load} title="刷新">
            <RefreshCw size={14} />
          </button>
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-400/20 bg-red-400/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      {/* source 列表 */}
      <div className="space-y-2">
        {sources.length === 0 ? (
          <div className="panel-elevated rounded-md px-4 py-10 text-center text-xs text-text-tertiary">
            暂无书源
          </div>
        ) : (
          sources.map((s) => {
            const meta = STATUS_META[s.verification_status] ?? STATUS_META.experimental;
            const Icon = meta.icon;
            return (
              <div
                key={s.id}
                className="flex items-center gap-3 rounded-card border border-border bg-bg-panel px-3.5 py-3"
              >
                <span className="h-8 w-1 rounded-full" style={{ background: meta.dot }} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-body font-medium text-text-primary">{s.name}</span>
                    <span className="font-mono text-2xs text-text-disabled">{s.code}</span>
                  </div>
                  <div className="mt-0.5 truncate font-mono text-2xs text-text-tertiary">
                    {s.content_selector}
                  </div>
                </div>
                <span
                  className={clsx(
                    "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-2xs",
                    meta.cls
                  )}
                >
                  <Icon size={11} />
                  {meta.label}
                </span>
                {s.last_verified_at && (
                  <span className="hidden text-2xs text-text-disabled md:inline">
                    {new Date(s.last_verified_at).toLocaleDateString()}
                  </span>
                )}
                <button
                  onClick={() => onProbeSource?.(s)}
                  className="btn text-2xs py-1 px-2 flex items-center gap-1"
                  title="在采集页测试该书源"
                >
                  <Zap size={11} />
                  测试
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
