import { prisma } from "@/lib/db";
import { determineLevel } from "./level";

interface PromotionResult {
  promoted: boolean;
  fromLevel: number;
  toLevel: number;
}

/**
 * Check if a skill qualifies for promotion and update if so.
 * @param userId - User ID
 * @param skillId - Skill ID to check
 * @returns Promotion result or null if skill not found/access denied
 */
export async function checkAndPromote(
  userId: string,
  skillId: string
): Promise<PromotionResult | null> {
  const skill = await prisma.skill.findUnique({
    where: { id: skillId },
    include: { levels: { orderBy: { level: "asc" } } },
  });

  // Access control: ensure skill belongs to user
  if (!skill || skill.userId !== userId) {
    return null;
  }

  // Parse deliverable checklist from JSON
  const deliverableChecklist = skill.levels.map((level) => {
    let items: { text: string; done: boolean }[] = [];
    try {
      items = JSON.parse(level.requiredDeliverables);
    } catch {
      // Ignore parse errors, keep empty checklist
    }
    return { level: level.level, items };
  });

  // Determine current level based on hours and deliverables
  const result = determineLevel({
    effectiveHoursTotal: skill.effectiveHoursTotal,
    deliberateHoursTotal: skill.deliberateHoursTotal,
    levels: skill.levels,
    deliverableChecklist,
  });

  // Promote if qualified
  if (result.current > skill.currentLevel) {
    await prisma.skill.update({
      where: { id: skillId },
      data: { currentLevel: result.current },
    });
    return {
      promoted: true,
      fromLevel: skill.currentLevel,
      toLevel: result.current,
    };
  }

  return {
    promoted: false,
    fromLevel: skill.currentLevel,
    toLevel: skill.currentLevel,
  };
}
