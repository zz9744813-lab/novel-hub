import { prisma } from "@/lib/db";
import { determineLevel } from "./level";

export async function checkAndPromote(userId: string, skillId: string): Promise<{
  promoted: boolean;
  fromLevel: number;
  toLevel: number;
} | null> {
  const skill = await prisma.skill.findUnique({
    where: { id: skillId },
    include: { levels: { orderBy: { level: "asc" } } },
  });

  if (!skill || skill.userId !== userId) return null;

  const deliverableChecklist = skill.levels.map((lv) => {
    let items: { text: string; done: boolean }[] = [];
    try {
      items = JSON.parse(lv.requiredDeliverables);
    } catch {}
    return { level: lv.level, items };
  });

  const result = determineLevel({
    effectiveHoursTotal: skill.effectiveHoursTotal,
    deliberateHoursTotal: skill.deliberateHoursTotal,
    levels: skill.levels,
    deliverableChecklist,
  });

  if (result.current > skill.currentLevel) {
    await prisma.skill.update({
      where: { id: skillId },
      data: { currentLevel: result.current },
    });
    return { promoted: true, fromLevel: skill.currentLevel, toLevel: result.current };
  }

  return { promoted: false, fromLevel: skill.currentLevel, toLevel: skill.currentLevel };
}
