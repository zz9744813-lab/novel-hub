import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

const DEFAULT_LEVELS = [
  { level: 0, levelName: "未入门", minEffectiveHours: 0, maxEffectiveHours: 10, requiredDeliverables: "[]", assessmentCriteria: "只知道概念，不能独立完成" },
  { level: 1, levelName: "入门", minEffectiveHours: 10, maxEffectiveHours: 50, requiredDeliverables: "[]", assessmentCriteria: "能照着教程做，依赖模板" },
  { level: 2, levelName: "初级", minEffectiveHours: 50, maxEffectiveHours: 200, requiredDeliverables: "[]", assessmentCriteria: "能完成简单任务，错误较多" },
  { level: 3, levelName: "合格", minEffectiveHours: 200, maxEffectiveHours: 500, requiredDeliverables: "[]", assessmentCriteria: "能独立完成基础项目" },
  { level: 4, levelName: "熟练", minEffectiveHours: 500, maxEffectiveHours: 1000, requiredDeliverables: "[]", assessmentCriteria: "稳定交付，有自己的方法" },
  { level: 5, levelName: "专业", minEffectiveHours: 1000, maxEffectiveHours: 3000, requiredDeliverables: "[]", assessmentCriteria: "能解决复杂问题，可商用" },
  { level: 6, levelName: "专家", minEffectiveHours: 3000, maxEffectiveHours: 8000, requiredDeliverables: "[]", assessmentCriteria: "有体系化能力，能指导他人" },
  { level: 7, levelName: "大师候选", minEffectiveHours: 8000, maxEffectiveHours: 15000, requiredDeliverables: "[]", assessmentCriteria: "有代表作、方法论、风格" },
  { level: 8, levelName: "大师", minEffectiveHours: 15000, maxEffectiveHours: 99999, requiredDeliverables: "[]", assessmentCriteria: "稀缺能力 + 影响力 + 代表成果" },
];

async function main() {
  const existingUser = await prisma.user.findFirst();
  if (!existingUser) {
    const user = await prisma.user.create({
      data: {
        name: process.env.APP_USER_NAME || "User",
      },
    });
    console.log(`Created default user: ${user.name} (${user.id})`);
  } else {
    console.log(`User already exists: ${existingUser.name}`);
  }
  console.log("Seed complete.");
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });

export { DEFAULT_LEVELS };
