type QualityFactors = {
  baseMultiplier: number;
  hasOutput: boolean;
  isDeliberate: boolean;
  alignedWithCommitment: boolean;
  contextSwitchPenalty: number;
  qualityScore: number;
};

const CATEGORY_BASE: Record<string, number> = {
  creation: 0.85,
  skill_practice: 0.75,
  learning: 0.55,
  project: 0.80,
  body: 0.95,
  leisure: 0.15,
  drift: 0.05,
  other: 0.50,
};

export function getBaseMultiplier(category: string): number {
  return CATEGORY_BASE[category] ?? 0.50;
}

export function computeEffective(raw: number, f: QualityFactors): number {
  let m = f.baseMultiplier;
  if (f.hasOutput) m += 0.10;
  if (f.isDeliberate) m += 0.10;
  if (f.alignedWithCommitment) m += 0.05;
  m -= f.contextSwitchPenalty;
  if (f.qualityScore <= 2) m = Math.min(m, 0.20);
  if (f.qualityScore >= 9) m = Math.max(m, 0.85);
  m = Math.max(0, Math.min(1, m));
  return Number((raw * m).toFixed(2));
}

export function conversionRate(totalEffective: number, totalRaw: number): number {
  if (totalRaw === 0) return 0;
  return Number((totalEffective / totalRaw).toFixed(2));
}

export function conversionColor(rate: number): "red" | "yellow" | "green" {
  if (rate < 0.5) return "red";
  if (rate < 0.7) return "yellow";
  return "green";
}
