import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api";
import type {
  EditorialAnnotation,
  EditorialAnnotationInput,
  EditorialReviewDetail,
} from "../../api";
import {
  Loader2,
  X,
  MessageSquarePlus,
  Trash2,
  Check,
  Ban,
  Pencil,
  ThumbsUp,
  AlertTriangle,
  Sparkles,
  ClipboardCheck,
} from "lucide-react";

const ANNOTATION_TYPE_LABELS: Record<string, string> = {
  issue: "问题",
  suggestion: "建议",
  direct_edit: "直接改写",
  praise: "表扬",
  question: "疑问",
  preference: "偏好",
  forbidden_pattern: "禁用模式",
};

const SEVERITY_LABELS: Record<string, string> = {
  critical: "致命",
  major: "重要",
  minor: "轻微",
  note: "备注",
  praise: "好评",
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: "#f87171",
  major: "#fb923c",
  minor: "#facc15",
  note: "#94a3b8",
  praise: "#4ade80",
};

const VERDICT_LABELS: Record<string, string> = {
  accept: "通过",
  accept_with_notes: "通过（附批注）",
  revise: "退回修改",
  reject: "拒绝",
};

const REMEDIATION_LEVELS: Array<{ level: string; name: string; desc: string }> = [
  { level: "L0", name: "仅学习", desc: "不重生成，批注只进入学习沉淀" },
  { level: "L1", name: "局部润色", desc: "按批注逐点修复措辞与细节" },
  { level: "L2", name: "场景级重写", desc: "重写涉及的冲突场景，保留其余" },
  { level: "L3", name: "章节重写", desc: "保留大纲与契约，整章重写" },
  { level: "L4", name: "重规划重写", desc: "回到规划层重新规划再重写" },
  { level: "L5", name: "系统改进", desc: "升级到提示词/组件级修改建议" },
];

interface SelectionTarget {
  paragraphKey: number;
  quotedText: string;
  startOffset: number;
  endOffset: number;
  top: number;
  left: number;
}

type SideTab = "annotations" | "ai_issues" | "verdict";

export function ReviewWorkbench({
  reviewId,
  onClose,
  onVerdictSubmitted,
}: {
  reviewId: string;
  onClose: () => void;
  onVerdictSubmitted?: (verdict: string, roundId: string) => void;
}) {
  const [detail, setDetail] = useState<EditorialReviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sideTab, setSideTab] = useState<SideTab>("annotations");

  // annotation drafting
  const [selection, setSelection] = useState<SelectionTarget | null>(null);
  const [draftOpen, setDraftOpen] = useState(false);
  const [draft, setDraft] = useState<{
    annotation_type: string;
    severity: string;
    comment: string;
    suggested_text: string;
    is_blocking: boolean;
    scope: string;
  }>({
    annotation_type: "issue",
    severity: "minor",
    comment: "",
    suggested_text: "",
    is_blocking: false,
    scope: "local_span",
  });

  // scoring
  const [scores, setScores] = useState<Record<string, number>>({});
  const [overallComment, setOverallComment] = useState("");
  const [pendingVerdict, setPendingVerdict] = useState<string | null>(null);
  const [remediation, setRemediation] = useState("L1");
  const [submitMsg, setSubmitMsg] = useState<string | null>(null);

  const contentRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const d = await api.editorial.roundDetail(reviewId);
      setDetail(d);
      setError(null);
      if (d.round.rubric_scores) setScores(d.round.rubric_scores);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [reviewId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !draftOpen) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, draftOpen]);

  const submitted = detail?.round.status === "submitted";

  // ── text selection → annotation anchor ──
  const handleMouseUp = useCallback(() => {
    if (submitted || !detail) return;
    const sel = window.getSelection();
    const text = sel?.toString().trim() ?? "";
    if (!sel || sel.isCollapsed || text.length < 2 || text.length > 500) {
      setSelection(null);
      return;
    }
    for (let i = 0; i < detail.paragraphs.length; i++) {
      const start = detail.paragraphs[i].indexOf(text);
      if (start >= 0) {
        const rect = sel.getRangeAt(0).getBoundingClientRect();
        setSelection({
          paragraphKey: i,
          quotedText: text,
          startOffset: start,
          endOffset: start + text.length,
          top: rect.top,
          left: rect.left + rect.width / 2,
        });
        return;
      }
    }
    setSelection(null);
  }, [detail, submitted]);

  const openDraft = () => {
    setDraft((d) => ({ ...d, comment: "", suggested_text: "" }));
    setDraftOpen(true);
    setSideTab("annotations");
  };

  const addAnnotation = async () => {
    if (!selection) return;
    if (draft.annotation_type === "direct_edit" && !draft.suggested_text.trim()) {
      setSubmitMsg("直接改写需要填写建议文本");
      return;
    }
    setBusy(true);
    try {
      const payload: EditorialAnnotationInput = {
        annotation_type: draft.annotation_type,
        severity: draft.annotation_type === "praise" ? "praise" : draft.severity,
        scope: draft.scope,
        paragraph_key: selection.paragraphKey,
        start_offset: selection.startOffset,
        end_offset: selection.endOffset,
        quoted_text: selection.quotedText,
        comment: draft.comment.trim() || null,
        suggested_text: draft.suggested_text.trim() || null,
        is_blocking: draft.is_blocking,
      };
      await api.editorial.addAnnotation(reviewId, payload);
      window.getSelection()?.removeAllRanges();
      setSelection(null);
      setDraftOpen(false);
      setSubmitMsg(null);
      await load();
    } catch (e) {
      setSubmitMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const removeAnnotation = async (id: string) => {
    setBusy(true);
    try {
      await api.editorial.deleteAnnotation(id);
      await load();
    } catch (e) {
      setSubmitMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const dispositionAiIssue = async (issueId: string, action: "confirm" | "dismiss" | "correct") => {
    setBusy(true);
    try {
      await api.editorial.dispositionAiIssue(reviewId, issueId, action);
      await load();
    } catch (e) {
      setSubmitMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const scoreTotal = useMemo(() => {
    if (!detail) return 0;
    return detail.rubric.reduce((sum, dim) => sum + (scores[dim.key] ?? 0), 0);
  }, [detail, scores]);

  const applyQuickGrade = (band: "A" | "B" | "C" | "D") => {
    if (!detail) return;
    const ratio = { A: 0.95, B: 0.82, C: 0.66, D: 0.42 }[band];
    const next: Record<string, number> = {};
    for (const dim of detail.rubric) next[dim.key] = Math.round(dim.weight * ratio);
    setScores(next);
  };

  const submitVerdict = async (verdict: string) => {
    setBusy(true);
    try {
      await api.editorial.submitRound(reviewId, {
        verdict,
        rubric_scores: Object.keys(scores).length ? scores : undefined,
        overall_comment: overallComment.trim() || undefined,
      });
      setPendingVerdict(verdict);
      setSubmitMsg(null);
      await load();
      onVerdictSubmitted?.(verdict, reviewId);
    } catch (e) {
      setSubmitMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const triggerRevision = async () => {
    setBusy(true);
    try {
      await api.editorial.requestRevision(reviewId, remediation);
      setSubmitMsg(`已触发修订（${remediation}），可在任务中心查看进度`);
      await load();
    } catch (e) {
      setSubmitMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const annotationsByPara = useMemo(() => {
    const map = new Map<number, EditorialAnnotation[]>();
    for (const a of detail?.annotations ?? []) {
      if (a.paragraph_key == null) continue;
      const list = map.get(a.paragraph_key) ?? [];
      list.push(a);
      map.set(a.paragraph_key, list);
    }
    return map;
  }, [detail]);

  if (error) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-6">
        <div className="panel-elevated p-6 max-w-md space-y-3">
          <p className="text-sm text-danger">加载审核失败</p>
          <p className="text-xs text-text-tertiary break-all">{error}</p>
          <button className="btn px-3 py-1.5 text-xs" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
        <Loader2 size={24} className="animate-spin text-brand-accent" />
      </div>
    );
  }

  const { round, chapter } = detail;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/75 backdrop-blur-md animate-fade-in">
      {/* header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-white/10 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <ClipboardCheck size={18} className="text-brand-accent shrink-0" />
          <div className="min-w-0">
            <h2 className="text-sm text-text-primary truncate" style={{ fontWeight: 560 }}>
              第 {chapter.chapter_no ?? "?"} 章 · {chapter.title || "未命名"} · 第 {round.round_no} 轮审核
            </h2>
            <p className="text-2xs text-text-tertiary">
              {submitted
                ? `已提交裁决：${VERDICT_LABELS[round.verdict ?? ""] ?? round.verdict} · 总分 ${round.score_total ?? "-"} · 等级 ${round.grade ?? "-"}`
                : "选中正文文字即可添加批注；批改完成后在右侧提交裁决"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {submitted && pendingVerdict && (
            <span className="badge bg-brand-accent/15 border-brand-accent/30 text-brand-accent">
              裁决已提交
            </span>
          )}
          <button className="btn-ghost px-2.5 py-1.5 text-xs" onClick={onClose} title="关闭 (Esc)">
            <X size={14} />
          </button>
        </div>
      </div>

      {/* body */}
      <div className="flex flex-1 min-h-0">
        {/* manuscript */}
        <div className="flex-1 min-w-0 overflow-auto p-6" ref={contentRef} onMouseUp={handleMouseUp}>
          <div className="mx-auto max-w-2xl space-y-1 select-text">
            {detail.paragraphs.map((para, i) => {
              const anns = annotationsByPara.get(i) ?? [];
              const hasBlocking = anns.some((a) => a.is_blocking);
              return (
                <div key={i} className="group">
                  <p
                    className={`relative text-sm leading-7 text-text-primary/90 rounded px-2 py-1 -mx-2 transition-colors ${
                      hasBlocking ? "bg-danger/5" : "hover:bg-white/[0.03]"
                    }`}
                    data-para={i}
                  >
                    <span className="absolute -left-7 top-1.5 hidden group-hover:inline-block w-5 text-right text-2xs text-text-disabled tabular-nums">
                      {i}
                    </span>
                    {para}
                  </p>
                  {anns.map((a) => (
                    <div
                      key={a.id}
                      className="ml-2 my-1.5 pl-3 py-1.5 rounded-r-md space-y-0.5"
                      style={{ borderLeft: `2px solid ${SEVERITY_COLORS[a.severity] ?? "#94a3b8"}` }}
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-2xs" style={{ color: SEVERITY_COLORS[a.severity] }}>
                          {ANNOTATION_TYPE_LABELS[a.annotation_type] ?? a.annotation_type}
                          {a.is_blocking && <span className="text-danger ml-1">·阻塞</span>}
                        </span>
                        {a.comment && (
                          <span className="text-xs text-text-secondary">{a.comment}</span>
                        )}
                        {!submitted && (
                          <button
                            onClick={() => removeAnnotation(a.id)}
                            disabled={busy}
                            className="ml-auto opacity-0 group-hover:opacity-100 hover:text-danger text-text-disabled transition-opacity"
                            title="删除批注"
                          >
                            <Trash2 size={12} />
                          </button>
                        )}
                      </div>
                      {a.suggested_text && (
                        <p className="text-xs text-success/80">
                          建议改为：「{a.suggested_text}」
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>

        {/* floating annotate button on selection */}
        {selection && !draftOpen && (
          <button
            className="fixed z-60 btn-primary px-3 py-1.5 text-xs shadow-xl"
            style={{ top: selection.top - 38, left: selection.left - 46 }}
            onClick={openDraft}
          >
            <MessageSquarePlus size={13} />
            批注
          </button>
        )}

        {/* side panel */}
        <div className="w-[380px] shrink-0 border-l border-white/10 flex flex-col min-h-0">
          {/* tabs */}
          <div className="flex border-b border-white/10 shrink-0">
            {(
              [
                ["annotations", `批注 (${detail.annotations.length})`],
                ["ai_issues", `AI 问题 (${detail.ai_issues.length})`],
                ["verdict", submitted ? "修订" : "评分裁决"],
              ] as Array<[SideTab, string]>
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setSideTab(key)}
                className={`flex-1 px-2 py-2.5 text-xs transition-colors border-b-2 ${
                  sideTab === key
                    ? "text-brand-accent border-brand-accent"
                    : "text-text-tertiary border-transparent hover:text-text-secondary"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-auto p-4 space-y-4">
            {/* annotation draft form */}
            {draftOpen && selection ? (
              <div className="panel-elevated p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs text-text-primary" style={{ fontWeight: 560 }}>
                    新批注 · 第 {selection.paragraphKey} 段
                  </h4>
                  <button
                    className="text-text-disabled hover:text-text-secondary"
                    onClick={() => setDraftOpen(false)}
                  >
                    <X size={13} />
                  </button>
                </div>
                <p className="text-2xs text-text-tertiary panel-sunken rounded px-2 py-1.5 leading-5">
                  「{selection.quotedText.slice(0, 120)}
                  {selection.quotedText.length > 120 ? "…" : ""}」
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <span className="text-2xs text-text-tertiary">类型</span>
                    <select
                      className="input text-xs py-1.5 px-2 w-full"
                      value={draft.annotation_type}
                      onChange={(e) =>
                        setDraft({ ...draft, annotation_type: e.target.value })
                      }
                    >
                      {Object.entries(ANNOTATION_TYPE_LABELS).map(([v, l]) => (
                        <option key={v} value={v}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1">
                    <span className="text-2xs text-text-tertiary">严重度</span>
                    <select
                      className="input text-xs py-1.5 px-2 w-full"
                      value={draft.severity}
                      disabled={draft.annotation_type === "praise"}
                      onChange={(e) => setDraft({ ...draft, severity: e.target.value })}
                    >
                      {Object.entries(SEVERITY_LABELS).map(([v, l]) => (
                        <option key={v} value={v}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="block space-y-1">
                  <span className="text-2xs text-text-tertiary">评论</span>
                  <textarea
                    className="input text-xs py-1.5 px-2 w-full resize-none"
                    rows={3}
                    placeholder="为什么这里有问题 / 好在哪"
                    value={draft.comment}
                    onChange={(e) => setDraft({ ...draft, comment: e.target.value })}
                  />
                </label>
                {draft.annotation_type === "direct_edit" && (
                  <label className="block space-y-1">
                    <span className="text-2xs text-brand-accent">改写为（必填，将沉淀为偏好对）</span>
                    <textarea
                      className="input text-xs py-1.5 px-2 w-full resize-none"
                      rows={3}
                      placeholder="直接写出你期望的文本"
                      value={draft.suggested_text}
                      onChange={(e) => setDraft({ ...draft, suggested_text: e.target.value })}
                    />
                  </label>
                )}
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-1.5 text-2xs text-text-tertiary cursor-pointer">
                    <input
                      type="checkbox"
                      checked={draft.is_blocking}
                      onChange={(e) => setDraft({ ...draft, is_blocking: e.target.checked })}
                    />
                    阻塞项（必须修复）
                  </label>
                  <button
                    className="btn-primary px-3 py-1.5 text-xs disabled:opacity-50"
                    disabled={busy}
                    onClick={addAnnotation}
                  >
                    {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                    保存批注
                  </button>
                </div>
              </div>
            ) : null}

            {sideTab === "annotations" && (
              <div className="space-y-2">
                {!draftOpen && detail.annotations.length === 0 && (
                  <div className="text-center py-10 text-xs text-text-tertiary space-y-2">
                    <MessageSquarePlus size={20} className="mx-auto text-text-disabled" />
                    <p>暂无批注</p>
                    <p className="text-2xs text-text-disabled">在左侧正文中选中文字，点击「批注」</p>
                  </div>
                )}
                {detail.annotations.map((a) => (
                  <div
                    key={a.id}
                    className="panel rounded-lg p-3 space-y-1.5"
                    style={{ borderLeft: `2px solid ${SEVERITY_COLORS[a.severity] ?? "#94a3b8"}` }}
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-2xs" style={{ color: SEVERITY_COLORS[a.severity] }}>
                        {ANNOTATION_TYPE_LABELS[a.annotation_type] ?? a.annotation_type}
                        {a.is_blocking && <span className="text-danger ml-1">·阻塞</span>}
                      </span>
                      <span className="text-2xs text-text-disabled">
                        {a.paragraph_key != null ? `第 ${a.paragraph_key} 段` : a.scope}
                      </span>
                      {a.resolution_status !== "open" && (
                        <span className="badge bg-success/10 border-success/20 text-success text-2xs ml-auto">
                          {a.resolution_status === "resolved" ? "已解决" : a.resolution_status}
                        </span>
                      )}
                    </div>
                    {a.quoted_text && (
                      <p className="text-2xs text-text-disabled line-clamp-2">「{a.quoted_text}」</p>
                    )}
                    {a.comment && <p className="text-xs text-text-secondary">{a.comment}</p>}
                    {a.suggested_text && (
                      <p className="text-xs text-success/80">建议：{a.suggested_text}</p>
                    )}
                    {!submitted && (
                      <button
                        onClick={() => removeAnnotation(a.id)}
                        disabled={busy}
                        className="text-2xs text-text-disabled hover:text-danger flex items-center gap-1"
                      >
                        <Trash2 size={11} /> 删除
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {sideTab === "ai_issues" && (
              <div className="space-y-2">
                {detail.ai_issues.length === 0 && (
                  <div className="text-center py-10 text-xs text-text-tertiary space-y-2">
                    <Sparkles size={20} className="mx-auto text-text-disabled" />
                    <p>本章无 AI 审校问题</p>
                  </div>
                )}
                {detail.ai_issues.map((iss) => {
                  const dispositions = round.ai_issue_dispositions ?? {};
                  const mine = dispositions[iss.id];
                  const color =
                    iss.severity === "critical"
                      ? "text-danger"
                      : iss.severity === "major"
                        ? "text-warning"
                        : "text-text-secondary";
                  return (
                    <div key={iss.id} className="panel rounded-lg p-3 space-y-1.5">
                      <div className="flex items-center gap-2">
                        <AlertTriangle size={12} className={color} />
                        <span className={`text-xs ${color}`}>{iss.issue_type}</span>
                        {mine && (
                          <span className="badge bg-white/5 border-white/10 text-text-tertiary ml-auto text-2xs">
                            {mine === "confirmed"
                              ? "已确认"
                              : mine === "dismissed"
                                ? "已驳回"
                                : "已修正"}
                          </span>
                        )}
                      </div>
                      <p className="text-2xs text-text-tertiary leading-5">{iss.evidence}</p>
                      {iss.repair_instruction && (
                        <p className="text-2xs text-text-disabled">修复建议：{iss.repair_instruction}</p>
                      )}
                      {!mine && (
                        <div className="flex gap-1.5 pt-1">
                          <button
                            className="btn px-2 py-1 text-2xs"
                            disabled={busy || submitted}
                            onClick={() => dispositionAiIssue(iss.id, "confirm")}
                          >
                            <Check size={11} /> 确认
                          </button>
                          <button
                            className="btn px-2 py-1 text-2xs"
                            disabled={busy || submitted}
                            onClick={() => dispositionAiIssue(iss.id, "dismiss")}
                          >
                            <Ban size={11} /> 驳回
                          </button>
                          <button
                            className="btn px-2 py-1 text-2xs"
                            disabled={busy || submitted}
                            onClick={() => dispositionAiIssue(iss.id, "correct")}
                          >
                            <Pencil size={11} /> 修正
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
                {detail.ai_issues.length > 0 && (
                  <p className="text-2xs text-text-disabled leading-5">
                    处置结果用于校准 AI 审校：确认/驳回会计入召回与误报统计。
                  </p>
                )}
              </div>
            )}

            {sideTab === "verdict" && !submitted && (
              <div className="space-y-4">
                {/* rubric scoring */}
                <div className="panel rounded-lg p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs text-text-primary" style={{ fontWeight: 560 }}>
                      评分表
                    </h4>
                    <div className="flex gap-1">
                      {(["A", "B", "C", "D"] as const).map((b) => (
                        <button
                          key={b}
                          onClick={() => applyQuickGrade(b)}
                          className="btn px-2 py-0.5 text-2xs"
                          title={`快捷档 ${b}`}
                        >
                          {b}
                        </button>
                      ))}
                    </div>
                  </div>
                  {detail.rubric.map((dim) => {
                    const v = scores[dim.key] ?? 0;
                    return (
                      <div key={dim.key} className="space-y-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-text-secondary">
                            {dim.name}
                            <span className="text-text-disabled ml-1">/{dim.weight}</span>
                          </span>
                          <span className="text-text-primary tabular-nums w-6 text-right">{v}</span>
                        </div>
                        <input
                          type="range"
                          min={0}
                          max={dim.weight}
                          value={v}
                          onChange={(e) => setScores({ ...scores, [dim.key]: Number(e.target.value) })}
                          className="w-full accent-[#6b7aff] h-1"
                        />
                      </div>
                    );
                  })}
                  <div className="flex items-center justify-between pt-1 border-t border-white/10">
                    <span className="text-xs text-text-tertiary">总分</span>
                    <span className="text-base text-text-primary tabular-nums" style={{ fontWeight: 600 }}>
                      {scoreTotal}
                    </span>
                  </div>
                </div>

                <label className="block space-y-1">
                  <span className="text-2xs text-text-tertiary">总评（选填）</span>
                  <textarea
                    className="input text-xs py-2 px-2.5 w-full resize-none"
                    rows={3}
                    placeholder="整体印象、共性问题、下一步建议…"
                    value={overallComment}
                    onChange={(e) => setOverallComment(e.target.value)}
                  />
                </label>

                <div className="grid grid-cols-2 gap-2">
                  <button
                    className="btn-primary py-2 text-xs disabled:opacity-50"
                    disabled={busy}
                    onClick={() => submitVerdict("accept")}
                  >
                    <ThumbsUp size={13} /> 通过
                  </button>
                  <button
                    className="btn py-2 text-xs border-success/40 text-success hover:bg-success/10 disabled:opacity-50"
                    disabled={busy}
                    onClick={() => submitVerdict("accept_with_notes")}
                  >
                    通过（附批注）
                  </button>
                  <button
                    className="btn py-2 text-xs border-warning/40 text-warning hover:bg-warning/10 disabled:opacity-50"
                    disabled={busy}
                    onClick={() => submitVerdict("revise")}
                  >
                    退回修改
                  </button>
                  <button
                    className="btn-danger py-2 text-xs disabled:opacity-50"
                    disabled={busy}
                    onClick={() => submitVerdict("reject")}
                  >
                    拒绝
                  </button>
                </div>
              </div>
            )}

            {/* after submit: revision trigger */}
            {sideTab === "verdict" && submitted && (
              <div className="space-y-4">
                <div className="panel rounded-lg p-4 space-y-1.5">
                  <h4 className="text-xs text-text-primary" style={{ fontWeight: 560 }}>
                    本轮结果
                  </h4>
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div>
                      <p className="text-2xs text-text-tertiary">裁决</p>
                      <p className="text-xs text-text-primary">
                        {VERDICT_LABELS[round.verdict ?? ""] ?? round.verdict}
                      </p>
                    </div>
                    <div>
                      <p className="text-2xs text-text-tertiary">总分</p>
                      <p className="text-xs text-text-primary tabular-nums">{round.score_total ?? "-"}</p>
                    </div>
                    <div>
                      <p className="text-2xs text-text-tertiary">等级</p>
                      <p className="text-xs text-text-primary">{round.grade ?? "-"}</p>
                    </div>
                  </div>
                  {round.overall_comment && (
                    <p className="text-2xs text-text-tertiary leading-5 pt-1 border-t border-white/10 mt-2">
                      {round.overall_comment}
                    </p>
                  )}
                </div>

                {["revise", "reject", "accept_with_notes"].includes(round.verdict ?? "") &&
                  chapter.id &&
                  detail.chapter && (
                    <div className="panel-elevated p-4 space-y-3">
                      <h4 className="text-xs text-text-primary" style={{ fontWeight: 560 }}>
                        触发修订闭环
                      </h4>
                      <p className="text-2xs text-text-tertiary leading-5">
                        选择返工等级，系统将按批注重锚生成新版本并进入待复检。
                      </p>
                      <div className="space-y-1.5">
                        {REMEDIATION_LEVELS.map((lv) => (
                          <label
                            key={lv.level}
                            className={`flex items-start gap-2 p-2 rounded-md cursor-pointer border transition-colors ${
                              remediation === lv.level
                                ? "border-brand-accent/50 bg-brand-accent/10"
                                : "border-white/8 hover:bg-white/5"
                            }`}
                          >
                            <input
                              type="radio"
                              name="remediation"
                              className="mt-0.5"
                              checked={remediation === lv.level}
                              onChange={() => setRemediation(lv.level)}
                            />
                            <span className="min-w-0">
                              <span className="text-xs text-text-primary">
                                {lv.level} · {lv.name}
                              </span>
                              <span className="block text-2xs text-text-tertiary leading-4">{lv.desc}</span>
                            </span>
                          </label>
                        ))}
                      </div>
                      <button
                        className="btn-primary w-full py-2 text-xs disabled:opacity-50"
                        disabled={busy}
                        onClick={triggerRevision}
                      >
                        {busy ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
                        触发修订
                      </button>
                    </div>
                  )}
              </div>
            )}

            {submitMsg && (
              <p className="text-2xs text-warning leading-5 panel-sunken rounded px-3 py-2">{submitMsg}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
