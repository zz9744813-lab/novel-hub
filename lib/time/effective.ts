import type { Category } from "@/lib/types";

const CATEGORY_BASE: Record<Category, number> = {
  creation: 0.85,
  skill_practice: 0.75,
  learning: 0.55,
  project: 0.80,
  body: 0.95,
  leisure: 0.15,
  drift: 0.05,
  other: 0.50,
};

/**
 * Get the base multiplier for a given activity category.
 * @param category - The activity category
 * @returns Base multiplier value (0.05-0.95)
 */
export function getBaseMultiplier(category: Category): number {
  return CATEGORY_BASE[category] ?? 0.50;
}

/**
 * Compute effective hours based on raw hours and quality factors.
 * Formula: raw × (base + output_bonus + deliberate_bonus + commitment_alignment - context_switch_penalty)
 * @param raw - Raw hours spent
 * @param factors - Quality factors affecting the multiplier
 * @returns Effective hours (capped at 0-raw range)
 */
export function computeEffective(
  raw: number,
  factors: {
    baseMultiplier: number;
    hasOutput: boolean;
    isDeliberate: boolean;
    alignedWithCommitment: boolean;
    contextSwitchPenalty: number;
    qualityScore: number;
  }
): number {
  let multiplier = factors.baseMultiplier;

  // Apply bonuses
  if (factors.hasOutput) multiplier += 0.10;
  if (factors.isDeliberate) multiplier += 0.10;
  if (factors.alignedWithCommitment) multiplier += 0.05;

  // Apply penalties
  multiplier -= factors.contextSwitchPenalty;

  // Quality score constraints
  if (factors.qualityScore <= 2) multiplier = Math.min(multiplier, 0.20);
  if (factors.qualityScore >= 9) multiplier = Math.max(multiplier, 0.85);

  // Clamp to valid range
  multiplier = Math.max(0, Math.min(1, multiplier));

  return Number((raw * multiplier).toFixed(2));
}

/**
 * Calculate the conversion rate from raw to effective hours.
 * @param totalEffective - Total effective hours
 * @param totalRaw - Total raw hours
 * @returns Conversion rate (0-1)
 */
export function conversionRate(totalEffective: number, totalRaw: number): number {
  if (totalRaw === 0) return 0;
  return Number((totalEffective / totalRaw).toFixed(2));
}

/**
 * Get color indicator for conversion rate.
 * @param rate - Conversion rate (0-1)
 * @returns Color: red (<0.5), yellow (0.5-0.7), green (>=0.7)
 */
export function conversionColor(rate: number): "red" | "yellow" | "green" {
  if (rate < 0.5) return "red";
  if (rate < 0.7) return "yellow";
  return "green";
}
