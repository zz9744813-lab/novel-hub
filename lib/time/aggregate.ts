import { prisma } from "@/lib/db";

export type WeeklyAgg = {
  weekKey: string;
  rawHours: number;
  effectiveHours: number;
  conversionRate: number;
  bySkill: { skillId: string; name: string; raw: number; effective: number }[];
  byCategory: Record<string, number>;
  driftHours: number;
};

export async function getWeeklyAggregation(userId: string, weekStart: Date, weekEnd: Date): Promise<WeeklyAgg> {
  const logs = await prisma.timeLog.findMany({
    where: {
      userId,
      date: { gte: weekStart, lt: weekEnd },
      status: { notIn: ["pending"] },
    },
    include: { entries: { include: { skill: true } } },
  });

  let rawHours = 0;
  let effectiveHours = 0;
  let driftHours = 0;
  const bySkillMap = new Map<string, { name: string; raw: number; effective: number }>();
  const byCategory: Record<string, number> = {};

  for (const log of logs) {
    for (const entry of log.entries) {
      rawHours += entry.rawHours;
      effectiveHours += entry.effectiveHours;

      if (entry.category === "drift") driftHours += entry.rawHours;
      byCategory[entry.category] = (byCategory[entry.category] ?? 0) + entry.rawHours;

      if (entry.skillId && entry.skill) {
        const existing = bySkillMap.get(entry.skillId) ?? { name: entry.skill.name, raw: 0, effective: 0 };
        existing.raw += entry.rawHours;
        existing.effective += entry.effectiveHours;
        bySkillMap.set(entry.skillId, existing);
      }
    }
  }

  const bySkill = Array.from(bySkillMap.entries()).map(([skillId, data]) => ({
    skillId,
    ...data,
  }));

  const weekKey = `${weekStart.getFullYear()}-W${String(Math.ceil((weekStart.getDate()) / 7)).padStart(2, "0")}`;

  return {
    weekKey,
    rawHours: Number(rawHours.toFixed(2)),
    effectiveHours: Number(effectiveHours.toFixed(2)),
    conversionRate: rawHours > 0 ? Number((effectiveHours / rawHours).toFixed(2)) : 0,
    bySkill,
    byCategory,
    driftHours: Number(driftHours.toFixed(2)),
  };
}
