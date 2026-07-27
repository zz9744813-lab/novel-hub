/** Human-readable Chinese labels for agent_role keys (API keys stay English). */
export const AGENT_ROLE_LABELS: Record<string, string> = {
  aileak_judge: "泄漏审查",
  chapter_planner: "章节规划",
  draft_writer: "正文写作",
  drift_audit: "漂移审计",
  evidence_ranker: "证据排序",
  local_rewrite_editor: "局部改写",
  memory_compiler: "记忆编译",
  outline_parser: "大纲解析",
  query_planner: "检索规划",
  reference_analyzer: "参考分析",
  research_planner: "调研规划",
  research_synthesizer: "调研综合",
  review_agent: "章节审核",
  state_extractor: "状态提取",
  consistency_checker: "一致性检查",
  canon_extract: "定稿提取",
};

export function agentRoleLabel(role: string | null | undefined): string {
  if (!role) return "—";
  return AGENT_ROLE_LABELS[role] || role;
}
