import type { SkillLevel } from "@/lib/types";

interface SkillSnapshot {
  effectiveHoursTotal: number;
  deliberateHoursTotal: number;
  levels: SkillLevel[];
  deliverableChecklist: { level: number; items: { text: string; done: boolean }[] }[];
}

interface LevelDeterminationResult {
  current: number;
  nextLevel: number;
  hoursToNext: number;
  blockedBy: string[];
}

/**
 * Determine the current skill level based on hours and deliverables.
 * Formula: combined = effectiveHours + deliberateHours × 0.5
 * @param snapshot - Current skill state
 * @returns Level determination result
 */
export function determineLevel(snapshot: SkillSnapshot): LevelDeterminationResult {
  const combined = snapshot.effectiveHoursTotal + snapshot.deliberateHoursTotal * 0.5;

  // Find current level by checking hours and deliverables
  let current = 0;
  for (const level of snapshot.levels) {
    const hoursOk = combined >= level.minEffectiveHours;
    const deliverable = snapshot.deliverableChecklist.find((d) => d.level === level.level);
    const deliverablesOk = !deliverable || deliverable.items.every((item) => item.done);

    if (hoursOk && deliverablesOk) {
      current = level.level;
    } else {
      break;
    }
  }

  const next = Math.min(current + 1, 8);
  const nextLevel = snapshot.levels.find((l) => l.level === next);
  const hoursToNext = nextLevel ? Math.max(0, nextLevel.minEffectiveHours - combined) : 0;
  const nextDeliverables = snapshot.deliverableChecklist.find((d) => d.level === next)?.items ?? [];
  const blockedBy = nextDeliverables.filter((item) => !item.done).map((item) => item.text);

  return { current, nextLevel: next, hoursToNext, blockedBy };
}
