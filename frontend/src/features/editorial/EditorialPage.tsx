import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import type { EditorialPolicy, EditorialQueueCard } from "../../api";
import { ReviewWorkbench } from "./ReviewWorkbench";
import { QualityDashboard } from "./QualityDashboard";
import { ExperienceCardsPanel } from "./ExperienceCardsPanel";
import {
  Loader2,
  RefreshCw,
  ClipboardCheck,
  Settings2,
  ChevronDown,
  ChevronRight,
  Play,
  RotateCcw,
  Hourglass,
  AlertTriangle,
  ShieldCheck,
  CheckCircle2,
  XCircle,
  TrendingUp,
  Brain,
} from "lucide-react";

const STATUS_META: Record<string, { label: string; cls: string }> = {
  pending_review: { label: "待审", cls: "bg-warning/10 border-warning/30 text-warning" },
  in_review: { label: "审核中", cls: "bg-brand-accent/10 border-brand-accent/30 text-brand-accent" },
  accepted: { label: "已通过", cls: "bg-success/10 border-success/30 text-success" },
  accepted_with_notes: { label: "通过附注", cls: "bg-success/10 border-success/30 text-success" },
  revision_requested: { label: "待修改", cls: "bg-warning/15 border-warning/40 text-warning" },
  revising: { label: "修改中", cls: "bg-[#c084fc]/10 border-[#c084fc]/30 text-[#c084fc]" },
  awaiting_recheck: { label: "待复检", cls: "bg-cyan-400/10 border-cyan-400/30 text-cyan-300" },
  rejected: { label: "已拒绝", cls: "bg-danger/10 border-danger/30 text-danger" },
  waived: { label: "免审", cls: "bg-bg-panel/5 border-border-standard/15 text-text-tertiary" },
};

const MODE_META: Record<string, { label: string; desc: string }> = {
  blocking: { label: "阻塞模式", desc: "上一章人工通过前，流水线暂停生成" },
  windowed: { label: "窗口模式", desc: "允许超前生成，但最多超前 N 章未审" },
  learning_only: { label: "仅学习", desc: "不拦截生成，审核意见只影响未来章节" },
};

const FILTERS: Array<{ key: string; label: string }> = [
  { key: "pending", label: "待审" },
  { key: "recheck", label: "待复检" },
  { key: "accepted", label: "已通过" },
  { key: "rejected", label: "已拒绝" },
  { key: "all", label: "全部" },
];

function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: status, cls: "bg-bg-panel/5 border-border-standard/15 text-text-tertiary" };
}

function fmtHours(h: number): string {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))} 分钟`;
  if (h < 24) return `${Math.floor(h)} 小时`;
  return `${Math.floor(h / 24)} 天`;
}

export function EditorialPage({ bookId }: { bookId?: string }) {
  const [view, setView] = useState<"queue" | "quality" | "experience">("queue");
  const [policy, setPolicy] = useState<EditorialPolicy | null>(null);
  const [policyBookId, setPolicyBookId] = useState<string | undefined>(bookId);
  const [queue, setQueue] = useState<EditorialQueueCard[]>([]);
  const [filter, setFilter] = useState("pending");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPolicy, setShowPolicy] = useState(false);
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [activeReviewId, setActiveReviewId] = useState<string | null>(null);
  const [startingId, setStartingId] = useState<string | null>(null);

  const loadPolicy = useCallback(async (bid?: string) => {
    if (!bid) {
      setPolicy(null);
      return;
    }
    try {
      const p = await api.editorial.policy(bid);
      setPolicy(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadQueue = useCallback(
    async (silent = false) => {
      if (!silent) setRefreshing(true);
      try {
        const cards = await api.editorial.queue(filter, bookId);
        setQueue(cards);
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!silent) setRefreshing(false);
      }
    },
    [filter, bookId]
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      await Promise.all([loadPolicy(policyBookId), loadQueue(true)]);
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId]);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  const savePolicy = async (patch: Partial<EditorialPolicy>) => {
    if (!policyBookId) return;
    setSavingPolicy(true);
    try {
      const updated = await api.editorial.updatePolicy(policyBookId, patch);
      setPolicy(updated);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingPolicy(false);
    }
  };

  const startReview = async (card: EditorialQueueCard) => {
    setStartingId(card.chapter_id);
    try {
      // reuse an existing draft round, else create a new one
      const rounds = await api.editorial.listRounds(card.chapter_id);
      const draft = rounds.find((r) => r.status === "draft");
      const round = draft ?? (await api.editorial.createRound(card.chapter_id));
      setActiveReviewId(round.id);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStartingId(null);
    }
  };

  const pendingCount = queue.length;

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-5">
      {/* header */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-base text-text-primary flex items-center gap-2" style={{ fontWeight: 510 }}>
            <ClipboardCheck size={16} className="text-brand-accent" />
            人工审核
          </h1>
          <p className="text-xs text-text-tertiary mt-0.5">
            人工是事实标准：批改、圈注、裁决，每一条意见都进入学习闭环
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn-ghost px-2.5 py-1.5 text-xs flex items-center gap-1.5"
            onClick={() => setShowPolicy((v) => !v)}
          >
            {showPolicy ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            <Settings2 size={12} />
            审核政策
          </button>
          <button
            onClick={() => loadQueue()}
            disabled={refreshing}
            className="btn-ghost px-2.5 py-1.5 flex items-center gap-1.5 text-xs disabled:opacity-50"
          >
            <RefreshCw size={12} className={refreshing ? "animate-spin" : ""} />
            刷新
          </button>
        </div>
      </div>

      {error && (
        <div className="panel rounded-lg px-4 py-3 text-xs text-danger flex items-start gap-2">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span className="break-all">{error}</span>
        </div>
      )}

      {/* policy card */}
      {showPolicy && (
        <div className="panel-elevated rounded-card p-5 space-y-4 animate-fade-in">
          <div className="flex items-center justify-between">
            <h3 className="text-sm text-text-primary font-medium flex items-center gap-2">
              <ShieldCheck size={15} className="text-brand-accent" />
              审核政策 {policyBookId ? "" : "（选择一本书后可配置）"}
            </h3>
            {savingPolicy && <Loader2 size={13} className="animate-spin text-brand-accent" />}
          </div>

          {policy ? (
            <div className="space-y-4">
              {/* mode */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
                {Object.entries(MODE_META).map(([mode, meta]) => (
                  <button
                    key={mode}
                    disabled={savingPolicy}
                    onClick={() => savePolicy({ mode })}
                    className={`text-left p-3 rounded-lg border transition-all ${
                      policy.mode === mode
                        ? "border-brand-accent/50 bg-brand-accent/10"
                        : "border-border-standard/8 hover:bg-bg-hover/5"
                    }`}
                  >
                    <p className="text-xs text-text-primary" style={{ fontWeight: 540 }}>
                      {meta.label}
                    </p>
                    <p className="text-2xs text-text-tertiary mt-1 leading-4">{meta.desc}</p>
                  </button>
                ))}
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <label className="space-y-1">
                  <span className="text-2xs text-text-tertiary">最大超前章数</span>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    className="input text-xs py-1.5 px-2 w-full tabular-nums"
                    value={policy.max_unreviewed_ahead}
                    disabled={savingPolicy || policy.mode !== "windowed"}
                    onChange={(e) =>
                      setPolicy({ ...policy, max_unreviewed_ahead: Number(e.target.value) })
                    }
                    onBlur={(e) =>
                      savePolicy({ max_unreviewed_ahead: Number(e.target.value) })
                    }
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-2xs text-text-tertiary">优良分阈值</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    className="input text-xs py-1.5 px-2 w-full tabular-nums"
                    value={policy.good_score_threshold}
                    disabled={savingPolicy}
                    onChange={(e) =>
                      setPolicy({ ...policy, good_score_threshold: Number(e.target.value) })
                    }
                    onBlur={(e) => savePolicy({ good_score_threshold: Number(e.target.value) })}
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-2xs text-text-tertiary">良品率暂停线 (%)</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    className="input text-xs py-1.5 px-2 w-full tabular-nums"
                    value={policy.auto_pause_good_rate_threshold}
                    disabled={savingPolicy}
                    onChange={(e) =>
                      setPolicy({
                        ...policy,
                        auto_pause_good_rate_threshold: Number(e.target.value),
                      })
                    }
                    onBlur={(e) =>
                      savePolicy({ auto_pause_good_rate_threshold: Number(e.target.value) })
                    }
                  />
                </label>
                <label className="space-y-1">
                  <span className="text-2xs text-text-tertiary">连续差评暂停</span>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    className="input text-xs py-1.5 px-2 w-full tabular-nums"
                    value={policy.auto_pause_consecutive_bad}
                    disabled={savingPolicy}
                    onChange={(e) =>
                      setPolicy({ ...policy, auto_pause_consecutive_bad: Number(e.target.value) })
                    }
                    onBlur={(e) =>
                      savePolicy({ auto_pause_consecutive_bad: Number(e.target.value) })
                    }
                  />
                </label>
              </div>

              <div className="flex flex-wrap gap-4">
                <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
                  <input
                    type="checkbox"
                    checked={policy.experience_auto_activation}
                    disabled={savingPolicy}
                    onChange={(e) => savePolicy({ experience_auto_activation: e.target.checked })}
                  />
                  经验卡自动激活
                </label>
                <label className="flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
                  <input
                    type="checkbox"
                    checked={policy.low_risk_auto_promote}
                    disabled={savingPolicy}
                    onChange={(e) => savePolicy({ low_risk_auto_promote: e.target.checked })}
                  />
                  低风险批次自动晋级
                </label>
              </div>
            </div>
          ) : (
            <p className="text-xs text-text-tertiary py-2">
              {policyBookId
                ? "加载政策中…"
                : "政策按书配置。请先在书架中选择一本书，再回到此页配置。"}
            </p>
          )}
        </div>
      )}

      {/* view switcher */}
      <div className="flex items-center gap-1.5">
        {(
          [
            ["queue", "审核队列", ClipboardCheck],
            ["quality", "质量看板", TrendingUp],
            ["experience", "经验卡", Brain],
          ] as Array<["queue" | "quality" | "experience", string, typeof ClipboardCheck]>
        ).map(([key, label, Icon]) => (
          <button
            key={key}
            onClick={() => setView(key)}
            className={`px-3 py-1.5 rounded-full text-xs transition-all border flex items-center gap-1.5 ${
              view === key
                ? "bg-brand-accent/15 border-brand-accent/40 text-brand-accent"
                : "border-border-standard/10 text-text-tertiary hover:text-text-secondary hover:bg-bg-hover/5"
            }`}
          >
            <Icon size={12} />
            {label}
          </button>
        ))}
      </div>

      {view === "quality" && bookId && <QualityDashboard bookId={bookId} />}
      {view === "experience" && bookId && <ExperienceCardsPanel bookId={bookId} />}

      {view === "quality" && !bookId && (
        <div className="panel rounded-card py-12 text-center">
          <p className="text-xs text-text-tertiary">质量看板按书统计，请先在书架中选择一本书</p>
        </div>
      )}
      {view === "experience" && !bookId && (
        <div className="panel rounded-card py-12 text-center">
          <p className="text-xs text-text-tertiary">经验卡按书沉淀，请先在书架中选择一本书</p>
        </div>
      )}

      {view === "queue" && (
        <>
      {/* filter tabs */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`px-3 py-1.5 rounded-full text-xs transition-all border ${
              filter === f.key
                ? "bg-brand-accent/15 border-brand-accent/40 text-brand-accent"
                : "border-border-standard/10 text-text-tertiary hover:text-text-secondary hover:bg-bg-hover/5"
            }`}
          >
            {f.label}
            {f.key === filter && pendingCount > 0 ? ` (${pendingCount})` : ""}
          </button>
        ))}
      </div>

      {/* queue */}
      {loading ? (
        <div className="flex items-center justify-center py-16 text-text-tertiary text-xs gap-2">
          <Loader2 size={16} className="animate-spin" /> 加载审核队列…
        </div>
      ) : queue.length === 0 ? (
        <div className="panel rounded-card py-16 text-center space-y-2">
          <CheckCircle2 size={22} className="mx-auto text-text-disabled" />
          <p className="text-xs text-text-tertiary">
            {filter === "pending" ? "没有待审章节" : "当前筛选下没有章节"}
          </p>
          <p className="text-2xs text-text-disabled">
            由流水线定稿的章节会自动进入待审队列
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {queue.map((card) => {
            const meta = statusMeta(card.editorial_status);
            const actionable = ["pending_review", "in_review", "awaiting_recheck"].includes(
              card.editorial_status
            );
            return (
              <div
                key={card.chapter_id}
                className="panel rounded-card p-4 space-y-3 hover:border-border-strong/15 transition-colors"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-xs text-text-primary truncate" style={{ fontWeight: 540 }}>
                      第 {card.chapter_no} 章 · {card.title || "未命名"}
                    </p>
                    {!bookId && card.book_title && (
                      <p className="text-2xs text-text-tertiary truncate mt-0.5">
                        {card.book_title}
                      </p>
                    )}
                  </div>
                  <span className={`badge border ${meta.cls} shrink-0`}>{meta.label}</span>
                </div>

                <div className="flex items-center gap-3 text-2xs text-text-disabled">
                  <span className="flex items-center gap-1">
                    <Hourglass size={10} /> 等待 {fmtHours(card.waiting_hours)}
                  </span>
                  <span className="flex items-center gap-1">
                    <RotateCcw size={10} /> 第 {card.rounds} 轮
                  </span>
                  {card.ai_issue_count > 0 && (
                    <span className="flex items-center gap-1 text-warning">
                      <AlertTriangle size={10} /> AI 问题 {card.ai_issue_count}
                    </span>
                  )}
                </div>

                {actionable ? (
                  <button
                    className="btn-primary w-full py-1.5 text-xs disabled:opacity-50"
                    disabled={startingId === card.chapter_id}
                    onClick={() => startReview(card)}
                  >
                    {startingId === card.chapter_id ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : (
                      <Play size={12} />
                    )}
                    {card.editorial_status === "awaiting_recheck" || card.rounds > 0
                      ? "开始复检"
                      : "开始审核"}
                  </button>
                ) : (
                  <div className="flex items-center justify-center gap-1.5 text-2xs text-text-disabled py-1">
                    {card.editorial_status === "rejected" ? (
                      <XCircle size={11} />
                    ) : (
                      <CheckCircle2 size={11} />
                    )}
                    本章审核已完结
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
        </>
      )}

      {activeReviewId && (
        <ReviewWorkbench
          reviewId={activeReviewId}
          onClose={() => {
            setActiveReviewId(null);
            loadQueue(true);
            loadPolicy(policyBookId);
          }}
          onVerdictSubmitted={() => {
            loadQueue(true);
          }}
        />
      )}
    </div>
  );
}
