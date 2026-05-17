import { z } from "zod";

export const LogEntrySchema = z.object({
  activity: z.string(),
  category: z.enum(["creation", "learning", "skill_practice", "body", "project", "leisure", "drift", "other"]),
  raw_hours: z.number().min(0).max(24),
  effective_hours: z.number().min(0).max(24),
  quality_score: z.number().min(0).max(10),
  deliberate_practice: z.boolean(),
  output_evidence: z.string().nullable(),
  skill_id: z.string().nullable(),
  suggest_new_skill: z.string().nullable(),
  ai_reason: z.string(),
});

export const ParseLogResponseSchema = z.object({
  entries: z.array(LogEntrySchema),
  untracked_hours: z.number().min(0).max(24),
  overall_note: z.string(),
});

export const RealityCheckResponseSchema = z.object({
  verdict: z.enum(["pass", "warn", "block"]),
  difficulty_ai: z.number().int().min(1).max(5),
  reasoning: z.string().max(80),
  suggested_target_hours: z.number().nullable(),
  suggested_deadline: z.string().nullable(),
  risk_factors: z.array(z.string()),
});

export const DailyAnalysisResponseSchema = z.object({
  verdict: z.string().max(80),
  risk: z.string().max(80),
  order: z.string().max(80),
  key_findings: z.array(z.string()),
  tomorrow_focus: z.object({
    skill_id: z.string().nullable(),
    suggested_hours: z.number(),
    concrete_action: z.string(),
  }),
});

export const SkillAssessmentResponseSchema = z.object({
  ai_assessed_level: z.number().int().min(0).max(8),
  agree_with_system: z.boolean(),
  disagreement_reason: z.string().nullable(),
  strengths: z.array(z.string()),
  weaknesses: z.array(z.string()),
  next_level_deliverables: z.array(z.object({
    text: z.string(),
    verifiable_by: z.string(),
  })),
  next_3_actions: z.array(z.object({
    title: z.string(),
    estimated_hours: z.number(),
    rationale: z.string(),
  })),
  master_reference: z.string().nullable(),
});

export const WeeklyAnalysisResponseSchema = z.object({
  content: z.string(),
  key_findings: z.array(z.string()),
  problems: z.array(z.string()),
  recommendations: z.array(z.string()),
  risk_warnings: z.array(z.string()),
});

export type ParseLogResponse = z.infer<typeof ParseLogResponseSchema>;
export type RealityCheckResponse = z.infer<typeof RealityCheckResponseSchema>;
export type DailyAnalysisResponse = z.infer<typeof DailyAnalysisResponseSchema>;
export type SkillAssessmentResponse = z.infer<typeof SkillAssessmentResponseSchema>;
export type WeeklyAnalysisResponse = z.infer<typeof WeeklyAnalysisResponseSchema>;
