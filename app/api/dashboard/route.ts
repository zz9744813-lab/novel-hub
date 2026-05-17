import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function GET() {
  const user = await prisma.user.findFirst();
  if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayStr = today.toISOString().split("T")[0];
  const todayDate = new Date(todayStr + "T00:00:00+08:00");

  const [todayLog, skills, activeCommitments, latestDailyReport] = await Promise.all([
    prisma.timeLog.findUnique({
      where: { userId_date: { userId: user.id, date: todayDate } },
      include: { entries: { include: { skill: { select: { id: true, name: true } } } } },
    }),
    prisma.skill.findMany({
      where: { userId: user.id, isActive: true },
      include: { levels: { orderBy: { level: "asc" } } },
      orderBy: { effectiveHoursTotal: "desc" },
      take: 5,
    }),
    prisma.commitment.findMany({
      where: { userId: user.id, status: "active" },
      orderBy: { deadline: "asc" },
    }),
    prisma.report.findFirst({
      where: { userId: user.id, type: "daily" },
      orderBy: { generatedAt: "desc" },
    }),
  ]);

  // 7-day stats
  const sevenDaysAgo = new Date(today);
  sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
  const recentLogs = await prisma.timeLog.findMany({
    where: { userId: user.id, date: { gte: sevenDaysAgo }, status: { notIn: ["pending"] } },
    select: { totalRawHours: true, totalEffectiveHours: true, date: true },
  });
  const weekRaw = recentLogs.reduce((s, l) => s + (l.totalRawHours ?? 0), 0);
  const weekEffective = recentLogs.reduce((s, l) => s + (l.totalEffectiveHours ?? 0), 0);

  return NextResponse.json({
    ok: true,
    data: {
      user: { name: user.name, creditScore: user.creditScore, creditTier: user.creditTier },
      today: todayLog,
      skills,
      activeCommitments,
      latestDailyReport,
      weekStats: {
        rawHours: Number(weekRaw.toFixed(1)),
        effectiveHours: Number(weekEffective.toFixed(1)),
        conversionRate: weekRaw > 0 ? Number((weekEffective / weekRaw).toFixed(2)) : 0,
        daysTracked: recentLogs.length,
      },
    },
  });
}
