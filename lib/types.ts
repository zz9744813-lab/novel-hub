import { z } from "zod";
import {
  ParseLogResponseSchema,
  RealityCheckResponseSchema,
  DailyAnalysisResponseSchema,
  SkillAssessmentResponseSchema,
  WeeklyAnalysisResponseSchema,
} from "./ai/schemas";

// ==================== AI Response Types ====================
export type ParseLogResponse = z.infer<typeof ParseLogResponseSchema>;
export type RealityCheckResponse = z.infer<typeof RealityCheckResponseSchema>;
export type DailyAnalysisResponse = z.infer<typeof DailyAnalysisResponseSchema>;
export type SkillAssessmentResponse = z.infer<typeof SkillAssessmentResponseSchema>;
export type WeeklyAnalysisResponse = z.infer<typeof WeeklyAnalysisResponseSchema>;

// ==================== API Response Types ====================
export interface ApiResponse<T> {
  ok: boolean;
  data?: T;
  error?: {
    code: string;
    message: string;
    details?: unknown;
  };
}

export interface DashboardData {
  user: {
    name: string;
    creditScore: number;
    creditTier: string;
  };
  today: TimeLogWithEntries | null;
  skills: SkillWithLevels[];
  activeCommitments: Commitment[];
  latestDailyReport: Report | null;
  weekStats: {
    rawHours: number;
    effectiveHours: number;
    conversionRate: number;
    daysTracked: number;
  };
}

export interface TimeLogWithEntries {
  id: string;
  userId: string;
  date: string;
  rawInput: string;
  parsedAt: string | null;
  status: string;
  totalRawHours: number | null;
  totalEffectiveHours: number | null;
  conversionRate: number | null;
  untrackedHours: number | null;
  aiOverallNote: string | null;
  entries: LogEntryWithSkill[];
}

export interface LogEntryWithSkill {
  id: string;
  timeLogId: string;
  activity: string;
  category: string;
  rawHours: number;
  effectiveHours: number;
  qualityScore: number;
  deliberate: boolean;
  outputEvidence: string | null;
  aiReason: string | null;
  source: string;
  skillId: string | null;
  skill: { id: string; name: string } | null;
}

export interface SkillWithLevels {
  id: string;
  userId: string;
  name: string;
  category: string;
  description: string | null;
  isActive: boolean;
  rawHoursTotal: number;
  effectiveHoursTotal: number;
  deliberateHoursTotal: number;
  currentLevel: number;
  targetLevel: number;
  lastAssessedAt: string | null;
  aiComment: string | null;
  strengths: string | null;
  weaknesses: string | null;
  levels: SkillLevel[];
}

export interface SkillLevel {
  id: string;
  skillId: string;
  level: number;
  levelName: string;
  minEffectiveHours: number;
  maxEffectiveHours: number;
  requiredDeliverables: string;
  assessmentCriteria: string;
  masterReference: string | null;
}

export interface Commitment {
  id: string;
  userId: string;
  title: string;
  type: string;
  startDate: string;
  deadline: string;
  targetHours: number;
  actualHours: number;
  completionStandard: string;
  difficulty: number;
  realityCheckPass: boolean;
  realityCheckNote: string | null;
  status: string;
  completionRate: number | null;
  onTime: boolean | null;
  retroNote: string | null;
  aiReview: string | null;
  creditDelta: number | null;
  linkedSkillId: string | null;
}

export interface Report {
  id: string;
  userId: string;
  type: string;
  scopeKey: string;
  generatedAt: string;
  inputSummary: string;
  content: string;
  keyFindings: string;
  problems: string;
  recommendations: string;
  riskWarnings: string;
}

// ==================== Domain Types ====================
export type CreditTier = "S" | "A" | "B" | "C" | "D";

export type Category =
  | "creation"
  | "learning"
  | "skill_practice"
  | "body"
  | "project"
  | "leisure"
  | "drift"
  | "other";

export type SkillLevelName =
  | "未入门"
  | "入门"
  | "初级"
  | "合格"
  | "熟练"
  | "专业"
  | "专家"
  | "大师候选"
  | "大师";

export interface LevelDefinition {
  level: number;
  levelName: SkillLevelName;
  minEffectiveHours: number;
  description: string;
}

// Standard L0-L8 level definitions
export const STANDARD_LEVELS: LevelDefinition[] = [
  { level: 0, levelName: "未入门", minEffectiveHours: 0, description: "尚未开始系统学习" },
  { level: 1, levelName: "入门", minEffectiveHours: 10, description: "掌握基础概念" },
  { level: 2, levelName: "初级", minEffectiveHours: 50, description: "能完成简单任务" },
  { level: 3, levelName: "合格", minEffectiveHours: 150, description: "独立完成常规工作" },
  { level: 4, levelName: "熟练", minEffectiveHours: 300, description: "高效解决常见问题" },
  { level: 5, levelName: "专业", minEffectiveHours: 600, description: "处理复杂场景" },
  { level: 6, levelName: "专家", minEffectiveHours: 1200, description: "领域内权威" },
  { level: 7, levelName: "大师候选", minEffectiveHours: 2500, description: "开创性贡献" },
  { level: 8, levelName: "大师", minEffectiveHours: 5000, description: "定义行业标准" },
];

// ==================== Aggregation Types ====================
export interface WeeklyAgg {
  weekKey: string;
  rawHours: number;
  effectiveHours: number;
  conversionRate: number;
  bySkill: { skillId: string; name: string; raw: number; effective: number }[];
  byCategory: Record<string, number>;
  driftHours: number;
}

// ==================== Utility Types ====================
export type SettlementStatus =
  | "completed"
  | "late_completed"
  | "partial"
  | "failed"
  | "abandoned"
  | "adjusted";
