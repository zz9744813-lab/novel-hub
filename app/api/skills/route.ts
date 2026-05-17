import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { DEFAULT_LEVELS } from "../../../../../prisma/seed";

export async function GET() {
  const user = await prisma.user.findFirst();
  if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

  const skills = await prisma.skill.findMany({
    where: { userId: user.id, isActive: true },
    include: { levels: { orderBy: { level: "asc" } } },
    orderBy: { effectiveHoursTotal: "desc" },
  });

  return NextResponse.json({ ok: true, data: skills });
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { name, category, description, targetLevel } = body;

    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

    const existing = await prisma.skill.findUnique({
      where: { userId_name: { userId: user.id, name } },
    });
    if (existing) {
      return NextResponse.json({ ok: false, error: { code: "CONSTRAINT", message: "Skill already exists" } }, { status: 409 });
    }

    const skill = await prisma.skill.create({
      data: {
        userId: user.id,
        name,
        category,
        description,
        targetLevel: targetLevel ?? 5,
        levels: {
          create: DEFAULT_LEVELS.map((lv) => ({
            level: lv.level,
            levelName: lv.levelName,
            minEffectiveHours: lv.minEffectiveHours,
            maxEffectiveHours: lv.maxEffectiveHours,
            requiredDeliverables: lv.requiredDeliverables,
            assessmentCriteria: lv.assessmentCriteria,
          })),
        },
      },
      include: { levels: true },
    });

    return NextResponse.json({ ok: true, data: skill });
  } catch (error) {
    return NextResponse.json({ ok: false, error: { code: "INTERNAL" } }, { status: 500 });
  }
}
