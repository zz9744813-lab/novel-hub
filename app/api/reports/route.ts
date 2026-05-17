import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET(req: NextRequest) {
  const type = req.nextUrl.searchParams.get("type");
  const user = await prisma.user.findFirst();
  if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

  const reports = await prisma.report.findMany({
    where: {
      userId: user.id,
      ...(type ? { type } : {}),
    },
    orderBy: { generatedAt: "desc" },
    take: 50,
  });

  return NextResponse.json({ ok: true, data: reports });
}
