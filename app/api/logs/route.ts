import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(req: NextRequest) {
  const date = req.nextUrl.searchParams.get("date");
  const user = await prisma.user.findFirst();
  if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

  if (date) {
    const dateObj = new Date(date + "T00:00:00+08:00");
    const log = await prisma.timeLog.findUnique({
      where: { userId_date: { userId: user.id, date: dateObj } },
      include: { entries: { include: { skill: { select: { id: true, name: true } } } } },
    });
    return NextResponse.json({ ok: true, data: log });
  }

  const logs = await prisma.timeLog.findMany({
    where: { userId: user.id },
    orderBy: { date: "desc" },
    take: 30,
    include: { entries: true },
  });
  return NextResponse.json({ ok: true, data: logs });
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { date, rawInput } = body;
    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

    const dateObj = new Date(date + "T00:00:00+08:00");
    const log = await prisma.timeLog.upsert({
      where: { userId_date: { userId: user.id, date: dateObj } },
      create: { userId: user.id, date: dateObj, rawInput, status: "pending" },
      update: { rawInput, status: "pending" },
    });

    return NextResponse.json({ ok: true, data: log });
  } catch (error) {
    return NextResponse.json({ ok: false, error: { code: "INTERNAL" } }, { status: 500 });
  }
}
