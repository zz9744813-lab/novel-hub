import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params;
  const user = await prisma.user.findFirst();
  if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

  const skill = await prisma.skill.findUnique({
    where: { id },
    include: {
      levels: { orderBy: { level: "asc" } },
      logEntries: {
        orderBy: { timeLog: { date: "desc" } },
        take: 50,
        include: { timeLog: { select: { date: true } } },
      },
    },
  });

  if (!skill || skill.userId !== user.id) {
    return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });
  }

  return NextResponse.json({ ok: true, data: skill });
}

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await req.json();

    const skill = await prisma.skill.findUnique({ where: { id } });
    if (!skill) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

    const updated = await prisma.skill.update({
      where: { id },
      data: {
        ...(body.name !== undefined && { name: body.name }),
        ...(body.description !== undefined && { description: body.description }),
        ...(body.targetLevel !== undefined && { targetLevel: body.targetLevel }),
        ...(body.isActive !== undefined && { isActive: body.isActive }),
      },
    });

    return NextResponse.json({ ok: true, data: updated });
  } catch (error) {
    return NextResponse.json({ ok: false, error: { code: "INTERNAL" } }, { status: 500 });
  }
}
