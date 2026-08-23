import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import type { EditorialExperienceCardItem } from "../../api";
import {
  Loader2,
  RefreshCw,
  Brain,
  Check,
  Lock,
  Ban,
  Eye,
  Zap,
} from "lucide-react";

const STATUS_TABS: Array<{ key: string; label: string }> = [
  { key: "candidate", label: "候选" },
  { key: "active", label: "已激活" },
  { key: "all", label: "全部" },
];

const RULE_LABELS: Record<string, string> = {
  preference: "偏好",
  anti_pattern: "反模式",
  positive_pattern: "有效模式",
  character_rule: "角色规则",
  scene_mode_rule: "场景规则",
  review_rule: "审校规则",
  planning_rule: "规划规则",
  style_rule: "文风规则",
};

const RULE_COLORS: Record<string, string> = {
  preference: "#7c8aff",
  anti_pattern: "#f87171",
  positive_pattern: "#4ade80",
};

export function ExperienceCardsPanel({ bookId }: { bookId: string }) {
  const [cards, setCards] = useState<EditorialExperienceCardItem[]>([]);
  const [tab, setTab] = useState("candidate");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // injection preview
  const [previewChapter, setPreviewChapter] = useState("");
  const [previewBlock, setPreviewBlock] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setRefreshing(true);
      try {
        const status = tab === "all" ? undefined : tab;
        setCards(await api.editorial.experienceCards(bookId, status));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!silent) setRefreshing(false);
      }
    },
    [bookId, tab]
  );

  useEffect(() => {
    setLoading(true);
    load(true).finally(() => setLoading(false));
  }, [load]);

  const updateCard = async (card: EditorialExperienceCardItem, status: string, lock?: boolean) => {
    setBusyId(card.id);
    try {
      await api.editorial.updateExperienceCard(card.id, { status, is_locked: lock ?? null });
      await load(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  };

  const runPreview = async () => {
    setPreviewing(true);
    try {
      const result = await api.editorial.previewExperience(bookId, {
        chapter_no: previewChapter ? Number(previewChapter) : null,
      });
      setPreviewBlock(result.prompt_block || "（当前无可注入的经验卡）");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewing(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm text-text-secondary font-medium flex items-center gap-2">
          <Brain size={14} className="text-brand-accent" />
          经验卡（错题本）
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
        <div className="panel rounded-lg px-4 py-3 text-xs text-danger">{error}</div>
      )}

      <p className="text-2xs text-text-tertiary leading-5">
        人工批注自动聚合为可检索的写作规则；被激活的卡片会在后续章节生成时注入提示词。
        重复出现的问题合并计数，支持越多置信度越高。
      </p>

      {/* tabs */}
      <div className="flex items-center gap-1.5">
        {STATUS_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-3 py-1.5 rounded-full text-xs transition-all border ${
              tab === t.key
                ? "bg-brand-accent/15 border-brand-accent/40 text-brand-accent"
                : "border-white/10 text-text-tertiary hover:text-text-secondary hover:bg-white/5"
            }`}
          >
            {t.label}
            {t.key === tab ? ` (${cards.length})` : ""}
          </button>
        ))}
      </div>

      {/* injection preview */}
      <div className="panel-elevated rounded-card p-4 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs text-text-primary flex items-center gap-1.5" style={{ fontWeight: 560 }}>
            <Eye size={13} className="text-brand-accent" />
            注入预览
          </h3>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              placeholder="章号"
              className="input text-xs py-1.5 px-2 w-20 tabular-nums"
              value={previewChapter}
              onChange={(e) => setPreviewChapter(e.target.value)}
            />
            <button
              className="btn-primary px-3 py-1.5 text-xs disabled:opacity-50"
              disabled={previewing}
              onClick={runPreview}
            >
              {previewing ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
              预览
            </button>
          </div>
        </div>
        {previewBlock && (
          <pre className="panel-sunken rounded px-3 py-2.5 text-2xs text-text-secondary leading-5 whitespace-pre-wrap max-h-56 overflow-auto">
            {previewBlock}
          </pre>
        )}
      </div>

      {/* cards */}
      {loading ? (
        <div className="flex items-center justify-center py-12 text-text-tertiary text-xs gap-2">
          <Loader2 size={16} className="animate-spin" /> 加载经验卡…
        </div>
      ) : cards.length === 0 ? (
        <div className="panel rounded-card py-12 text-center space-y-2">
          <Brain size={20} className="mx-auto text-text-disabled" />
          <p className="text-xs text-text-tertiary">
            {tab === "candidate" ? "暂无候选经验卡" : tab === "active" ? "暂无激活经验卡" : "暂无经验卡"}
          </p>
          <p className="text-2xs text-text-disabled">提交审核裁决后，批注会自动聚合为经验卡</p>
        </div>
      ) : (
        <div className="space-y-2">
          {cards.map((card) => (
            <div key={card.id} className="panel rounded-card p-4 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <span
                    className="badge border text-2xs"
                    style={{
                      color: RULE_COLORS[card.rule_type] ?? "#94a3b8",
                      borderColor: `${RULE_COLORS[card.rule_type] ?? "#94a3b8"}55`,
                      background: `${RULE_COLORS[card.rule_type] ?? "#94a3b8"}14`,
                    }}
                  >
                    {RULE_LABELS[card.rule_type] ?? card.rule_type}
                  </span>
                  <span className="text-2xs text-text-tertiary">{card.category}</span>
                  {card.status === "active" && (
                    <span className="badge bg-success/10 border-success/20 text-success text-2xs">
                      已激活
                    </span>
                  )}
                  {card.is_locked && (
                    <span className="badge bg-white/5 border-white/15 text-text-tertiary text-2xs">
                      <Lock size={9} /> 锁定
                    </span>
                  )}
                </div>
                <span className="text-2xs text-text-disabled tabular-nums shrink-0">
                  支持 {card.support_count} · 置信 {(card.confidence * 100).toFixed(0)}%
                </span>
              </div>

              <p className="text-xs text-text-primary leading-5">{card.instruction}</p>

              {card.rationale && card.rationale !== card.instruction && (
                <p className="text-2xs text-text-tertiary leading-4">{card.rationale}</p>
              )}

              <div className="flex items-center justify-between pt-1 border-t border-white/5">
                <span className="text-2xs text-text-disabled">
                  作用组件：{card.target_components.join(" / ") || "—"}
                </span>
                <div className="flex gap-1.5">
                  {card.status === "candidate" && (
                    <button
                      className="btn px-2 py-1 text-2xs border-success/40 text-success hover:bg-success/10 disabled:opacity-50"
                      disabled={busyId === card.id}
                      onClick={() => updateCard(card, "active")}
                    >
                      <Check size={11} /> 激活
                    </button>
                  )}
                  {card.status === "active" && !card.is_locked && (
                    <button
                      className="btn px-2 py-1 text-2xs"
                      disabled={busyId === card.id}
                      onClick={() => updateCard(card, "locked", true)}
                    >
                      <Lock size={11} /> 锁定
                    </button>
                  )}
                  {card.status !== "rejected" && (
                    <button
                      className="btn px-2 py-1 text-2xs hover:text-danger"
                      disabled={busyId === card.id}
                      onClick={() => updateCard(card, "rejected")}
                    >
                      <Ban size={11} /> 拒绝
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
