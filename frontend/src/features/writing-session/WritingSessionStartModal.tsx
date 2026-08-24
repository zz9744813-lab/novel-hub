import { useMemo, useState } from "react";
import { Loader2, Play } from "lucide-react";

export interface StartOptions {
  mode: "duration" | "until_time" | "manual";
  duration_minutes: number;
  until_time: string;
  max_unreviewed_ahead: number;
  stop_on_needs_human: boolean;
  stop_on_causal_failure: boolean;
  stop_on_quality_drop: boolean;
  stop_on_resource_block: boolean;
  quality_window_size: number;
  quality_min_sample: number;
  minimum_first_pass_yield: number;
  consecutive_bad_limit: number;
}

const DURATION_OPTIONS = [
  { label: "1 小时", minutes: 60 },
  { label: "2 小时", minutes: 120 },
  { label: "4 小时", minutes: 240 },
  { label: "8 小时", minutes: 480 },
];

export function WritingSessionStartModal({
  onStart,
  onClose,
}: {
  onStart: (options: StartOptions) => Promise<void>;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"duration" | "until_time" | "manual">("duration");
  const [durationMinutes, setDurationMinutes] = useState(240);
  const [untilTime, setUntilTime] = useState("23:00");
  const [maxUnreviewedAhead, setMaxUnreviewedAhead] = useState(5);
  const [stopNeedsHuman, setStopNeedsHuman] = useState(true);
  const [stopCausal, setStopCausal] = useState(true);
  const [stopQuality, setStopQuality] = useState(true);
  const [stopResource, setStopResource] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const hasDuration = useMemo(() => mode === "duration" && durationMinutes > 0, [mode, durationMinutes]);

  const submit = async () => {
    if (!hasDuration && !untilTime.trim()) {
      setErr(mode === "until_time" ? "请填写结束时间（HH:MM）" : "请选择运行时长");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await onStart({
        mode,
        duration_minutes: durationMinutes,
        until_time: untilTime.trim(),
        max_unreviewed_ahead: maxUnreviewedAhead,
        stop_on_needs_human: stopNeedsHuman,
        stop_on_causal_failure: stopCausal,
        stop_on_quality_drop: stopQuality,
        stop_on_resource_block: stopResource,
        quality_window_size: 10,
        quality_min_sample: 5,
        minimum_first_pass_yield: 0.7,
        consecutive_bad_limit: 2,
      });
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="panel-elevated rounded-xl w-full max-w-md p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm text-text-primary">自动写作</h2>
          <button onClick={onClose} className="text-xs text-text-tertiary hover:text-text-primary">关闭</button>
        </div>

        <div className="space-y-2">
          <div className="text-2xs text-text-disabled">运行方式</div>
          {DURATION_OPTIONS.map((opt) => (
            <label key={opt.minutes} className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
              <input
                type="radio"
                name="ws-mode"
                checked={mode === "duration" && durationMinutes === opt.minutes}
                onChange={() => { setMode("duration"); setDurationMinutes(opt.minutes); }}
              />
              {opt.label}
            </label>
          ))}
          <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
            <input
              type="radio"
              name="ws-mode"
              checked={mode === "until_time"}
              onChange={() => setMode("until_time")}
            />
            写到指定时间
            {mode === "until_time" && (
              <input
                type="time"
                value={untilTime}
                onChange={(e) => setUntilTime(e.target.value)}
                className="rounded border border-border bg-bg-base px-2 py-0.5 text-xs ml-1"
              />
            )}
          </label>
          <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
            <input
              type="radio"
              name="ws-mode"
              checked={mode === "manual"}
              onChange={() => setMode("manual")}
            />
            持续运行（人工停止）
          </label>
        </div>

        <div className="space-y-1.5">
          <label className="flex items-center justify-between text-xs text-text-secondary">
            <span>人工审核保护 · 最大未审核章节</span>
            <input
              type="number"
              min={0}
              max={100}
              value={maxUnreviewedAhead}
              onChange={(e) => setMaxUnreviewedAhead(Math.max(0, Number(e.target.value) || 0))}
              className="w-16 rounded border border-border bg-bg-base px-2 py-0.5 text-right"
            />
          </label>
          {[
            { label: "NEEDS_HUMAN 暂停", value: stopNeedsHuman, set: setStopNeedsHuman },
            { label: "CCNE Hard Block 暂停", value: stopCausal, set: setStopCausal },
            { label: "质量下降（最近10章首轮良品率 < 70%）暂停", value: stopQuality, set: setStopQuality },
            { label: "资源硬阻断暂停", value: stopResource, set: setStopResource },
          ].map((item) => (
            <label key={item.label} className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
              <input type="checkbox" checked={item.value} onChange={(e) => item.set(e.target.checked)} />
              {item.label}
            </label>
          ))}
        </div>

        <div className="text-2xs text-text-disabled">到时行为：当前章完成后停止</div>

        {err && <div className="text-xs text-red-400">{err}</div>}

        <button
          onClick={submit}
          disabled={busy}
          className="btn-primary text-xs py-2 px-4 w-full flex items-center justify-center gap-1.5"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
          开始自动写作
        </button>
      </div>
    </div>
  );
}
