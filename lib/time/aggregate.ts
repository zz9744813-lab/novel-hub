import type { WeeklyAgg } from "@/lib/types";
import { prisma } from "@/lib/db";

/**
 * Get aggregated statistics for a given week.
 * @param userId - User ID
 * @param weekStart - Start of the week (inclusive)
 * @param weekEnd - End of the week (exclusive)
 * @returns Weekly aggregation data
 */
export async function getWeeklyAggregation(
  userId: string,
  weekStart: Date,
  weekEnd: Date
): Promise<WeeklyAgg> {
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

  // Aggregate data from all logs
  for (const log of logs) {
    for (const entry of log.entries) {
      rawHours += entry.rawHours;
      effectiveHours += entry.effectiveHours;

      // Track drift hours
      if (entry.category === "drift") {
        driftHours += entry.rawHours;
      }

      // Aggregate by category
      byCategory[entry.category] = (byCategory[entry.category] ?? 0) + entry.rawHours;

      // Aggregate by skill
      if (entry.skillId && entry.skill) {
        const existing = bySkillMap.get(entry.skillId) ?? {
          name: entry.skill.name,
          raw: 0,
          effective: 0,
        };
        existing.raw += entry.rawHours;
        existing.effective += entry.effectiveHours;
        bySkillMap.set(entry.skillId, existing);
      }
    }
  }

  // Convert skill map to array
  const bySkill = Array.from(bySkillMap.entries()).map(([skillId, data]) => ({
    skillId,
    ...data,
  }));

  // Generate week key (e.g., "2024-W01")
  const weekNumber = Math.ceil(weekStart.getDate() / 7);
  const weekKey = `${weekStart.getFullYear()}-W${String(weekNumber).padStart(2, "0")}`;

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
