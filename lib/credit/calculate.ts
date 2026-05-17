type Settlement = {
  status: "completed" | "late_completed" | "partial" | "failed" | "abandoned" | "adjusted";
  completionRate: number;
  onTime: boolean;
  difficulty: number;
  hasRetrospection: boolean;
  estimateAccuracy: number;
};

const BASE_VALUE: Record<number, number> = { 1: 3, 2: 6, 3: 10, 4: 15, 5: 22 };

export function computeCreditDelta(s: Settlement): number {
  let delta = 0;
  const base = BASE_VALUE[s.difficulty] ?? 10;

  switch (s.status) {
    case "completed":
      delta = base * s.completionRate * (s.onTime ? 1 : 0.7);
      break;
    case "late_completed":
      delta = base * s.completionRate * 0.5;
      break;
    case "partial":
      delta = base * s.completionRate * 0.4 - base * 0.3;
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

  if (s.estimateAccuracy <= 0.15) delta += 2;
  if (s.estimateAccuracy > 0.5) delta -= 3;
  if (s.hasRetrospection) delta += 1.5;

  return Number(delta.toFixed(2));
}

export function applyCreditEvent(currentScore: number, delta: number): number {
  const ALPHA = 0.25;
  const newScore = currentScore + ALPHA * delta;
  return Math.min(150, Math.max(40, Number(newScore.toFixed(2))));
}

export function creditTier(score: number): "S" | "A" | "B" | "C" | "D" {
  if (score >= 120) return "S";
  if (score >= 100) return "A";
  if (score >= 80) return "B";
  if (score >= 60) return "C";
  return "D";
}
