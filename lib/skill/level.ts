type SkillSnapshot = {
  effectiveHoursTotal: number;
  deliberateHoursTotal: number;
  levels: {
    level: number;
    levelName: string;
    minEffectiveHours: number;
    maxEffectiveHours: number;
    requiredDeliverables: string;
    assessmentCriteria: string;
  }[];
  deliverableChecklist: { level: number; items: { text: string; done: boolean }[] }[];
};

export function determineLevel(s: SkillSnapshot): {
  current: number;
  nextLevel: number;
  hoursToNext: number;
  blockedBy: string[];
} {
  const combined = s.effectiveHoursTotal + s.deliberateHoursTotal * 0.5;

  let current = 0;
  for (const lv of s.levels) {
    const hoursOk = combined >= lv.minEffectiveHours;
    const deliv = s.deliverableChecklist.find((d) => d.level === lv.level);
    const delivOk = !deliv || deliv.items.every((i) => i.done);
    if (hoursOk && delivOk) current = lv.level;
    else break;
  }

  const next = Math.min(current + 1, 8);
  const nextLv = s.levels.find((l) => l.level === next);
  const hoursToNext = nextLv ? Math.max(0, nextLv.minEffectiveHours - combined) : 0;
  const nextDeliv = s.deliverableChecklist.find((d) => d.level === next)?.items ?? [];
  const blockedBy = nextDeliv.filter((i) => !i.done).map((i) => i.text);

  return { current, nextLevel: next, hoursToNext, blockedBy };
}
