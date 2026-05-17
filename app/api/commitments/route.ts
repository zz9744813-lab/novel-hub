import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(req: NextRequest) {
  const status = req.nextUrl.searchParams.get("status");
  const user = await prisma.user.findFirst();
  if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

  const commitments = await prisma.commitment.findMany({
    where: {
      userId: user.id,
      ...(status ? { status } : {}),
    },
    orderBy: { deadline: "asc" },
  });

  return NextResponse.json({ ok: true, data: commitments });
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { title, type, startDate, deadline, targetHours, completionStandard, difficulty, linkedSkillId } = body;

    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

    const commitment = await prisma.commitment.create({
      data: {
        userId: user.id,
        title,
        type,
        startDate: new Date(startDate),
        deadline: new Date(deadline),
        targetHours,
        completionStandard,
        difficulty,
        linkedSkillId: linkedSkillId || null,
        status: "active",
      },
    });

    return NextResponse.json({ ok: true, data: commitment });
  } catch (error) {
    return NextResponse.json({ ok: false, error: { code: "INTERNAL" } }, { status: 500 });
  }
}
