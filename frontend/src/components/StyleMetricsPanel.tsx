import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { StyleProfileOut } from "../api";
import { Gauge, Loader2, RefreshCw, Sparkles } from "lucide-react";

function MetricBar({
  label,
  value,
  max,
  unit,
}: {
  label: string;
  value: number;
  max: number;
  unit?: string;
}) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <span className="text-2xs text-text-tertiary">{label}</span>
        <span className="font-mono text-2xs text-text-secondary">
          {value}
          {unit}
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-bg-surface overflow-hidden">
        <div className="h-full rounded-full bg-brand-accent" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function DimensionCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-card border border-border bg-bg-panel p-3.5">
      <div className="mb-2.5 text-2xs font-medium uppercase tracking-wide text-text-secondary">
        {title}
      </div>
      <div className="space-y-2.5">{children}</div>
    </div>
  );
}

export function StyleMetricsPanel({ bookId }: { bookId: string }) {
  const [profile, setProfile] = useState<StyleProfileOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!bookId) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.styleProfile.get(bookId);
      setProfile(r.profile);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  useEffect(() => {
    load();
  }, [load]);

  const analyze = async () => {
    setAnalyzing(true);
    setError(null);
    try {
      const p = await api.styleProfile.analyze(bookId);
      setProfile(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 size={18} className="animate-spin text-text-disabled" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Gauge size={15} className="text-brand-accent" />
        <h3 className="text-sm font-medium text-text-primary">风格指标（可测量）</h3>
        {profile && (
          <span className="font-mono text-2xs text-text-disabled">
            v{profile.version} · {profile.metric_vector?.meta?.segment_count ?? "–"} 段采样
          </span>
        )}
        <button
          onClick={analyze}
          disabled={analyzing}
          className="ml-auto btn-primary text-2xs py-1.5 px-3 flex items-center gap-1.5"
        >
          {analyzing ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Sparkles size={12} />
          )}
          {profile ? "重新分析" : "生成风格档案"}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-400/20 bg-red-400/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      {!profile ? (
        <div className="panel-elevated rounded-card px-4 py-10 text-center">
          <p className="text-xs text-text-tertiary">
            尚未生成风格档案。点击「生成风格档案」从参考资料库的参考作品提取可测量的文风指标。
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <DimensionCard title="表层 · Surface">
            <MetricBar label="句长均值" value={profile.metric_vector.surface.sentence_chars_mean ?? 0} max={50} unit=" 字" />
            <MetricBar label="段长均值" value={profile.metric_vector.surface.paragraph_chars_mean ?? 0} max={200} unit=" 字" />
            <MetricBar label="词汇多样度" value={profile.metric_vector.surface.lexical_diversity ?? 0} max={1} />
            <MetricBar label="逗号密度" value={profile.metric_vector.surface.commas_per_1000 ?? 0} max={60} unit="/千字" />
          </DimensionCard>

          <DimensionCard title="节奏 · Rhythm">
            <MetricBar label="句长波动 (CV)" value={profile.metric_vector.rhythm.sentence_length_cv ?? 0} max={1.5} />
            <MetricBar label="短句占比" value={profile.metric_vector.rhythm.short_sentence_ratio ?? 0} max={1} />
            <MetricBar label="长句占比" value={profile.metric_vector.rhythm.long_sentence_ratio ?? 0} max={1} />
            <MetricBar label="段长波动 (CV)" value={profile.metric_vector.rhythm.paragraph_length_cv ?? 0} max={1.5} />
          </DimensionCard>

          <DimensionCard title="对话 · Dialogue">
            <MetricBar label="对话占比" value={profile.metric_vector.dialogue.dialogue_ratio ?? 0} max={1} />
            <MetricBar label="说话人切换率" value={profile.metric_vector.dialogue.speaker_switch_rate ?? 0} max={1} />
            <MetricBar label="对话回合中位长" value={profile.metric_vector.dialogue.dialogue_turn_length_p50 ?? 0} max={60} unit=" 字" />
          </DimensionCard>

          <DimensionCard title="情绪表达 · Emotion">
            <MetricBar label="显式情绪词密度" value={profile.metric_vector.emotion.explicit_emotion_word_ratio ?? 0} max={20} unit="/千字" />
            <MetricBar label="身体信号密度" value={profile.metric_vector.emotion.body_signal_ratio ?? 0} max={20} unit="/千字" />
            <MetricBar label="内心独白密度" value={profile.metric_vector.emotion.internal_monologue_ratio ?? 0} max={20} unit="/千字" />
          </DimensionCard>
        </div>
      )}
    </div>
  );
}
