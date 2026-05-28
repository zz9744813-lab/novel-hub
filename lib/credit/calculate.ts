import type { SettlementStatus } from "@/lib/types";

const BASE_VALUE: Record<number, number> = { 1: 3, 2: 6, 3: 10, 4: 15, 5: 22 };

/**
 * Compute credit delta based on commitment settlement.
 * @param settlement - Settlement details
 * @returns Credit score delta (can be negative)
 */
export function computeCreditDelta(settlement: {
  status: SettlementStatus;
  completionRate: number;
  onTime: boolean;
  difficulty: number;
  hasRetrospection: boolean;
  estimateAccuracy: number;
}): number {
  let delta = 0;
  const base = BASE_VALUE[settlement.difficulty] ?? 10;

  switch (settlement.status) {
    case "completed":
      delta = base * settlement.completionRate * (settlement.onTime ? 1 : 0.7);
      break;
    case "late_completed":
      delta = base * settlement.completionRate * 0.5;
      break;
    case "partial":
      delta = base * settlement.completionRate * 0.4 - base * 0.3;
      break;
    case "failed":
      delta = -base * 0.8;
      break;
    case "abandoned":
      delta = -base * 1.2;
      break;
    case "adjusted":
      delta = -base * 0.2;
      break;
  }

  // Estimate accuracy bonuses/penalties
  if (settlement.estimateAccuracy <= 0.15) delta += 2;
  if (settlement.estimateAccuracy > 0.5) delta -= 3;

  // Retrospection bonus
  if (settlement.hasRetrospection) delta += 1.5;

  return Number(delta.toFixed(2));
}

/**
 * Apply a credit event to the current score using EMA smoothing.
 * @param currentScore - Current credit score
 * @param delta - Credit delta from the event
 * @returns New credit score (clamped to 40-150)
 */
export function applyCreditEvent(currentScore: number, delta: number): number {
  const ALPHA = 0.25;
  const newScore = currentScore + ALPHA * delta;
  return Math.min(150, Math.max(40, Number(newScore.toFixed(2))));
}

/**
 * Get credit tier based on score.
 * @param score - Credit score
 * @returns Tier: S (>=120), A (>=100), B (>=80), C (>=60), D (<60)
 */
export function creditTier(score: number): "S" | "A" | "B" | "C" | "D" {
  if (score >= 120) return "S";
  if (score >= 100) return "A";
  if (score >= 80) return "B";
  if (score >= 60) return "C";
  return "D";
}
