import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(req: NextRequest) {
  const days = parseInt(req.nextUrl.searchParams.get("days") ?? "90");
  const user = await prisma.user.findFirst();
  if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

  const since = new Date();
  since.setDate(since.getDate() - days);

  const events = await prisma.creditEvent.findMany({
    where: { userId: user.id, occurredAt: { gte: since } },
    orderBy: { occurredAt: "desc" },
  });

  return NextResponse.json({ ok: true, data: events });
}
