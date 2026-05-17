import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getAnthropic, getModel } from "@/lib/ai/client";
import { DAILY_ANALYSIS_PROMPT } from "@/lib/ai/prompts";
import { DailyAnalysisResponseSchema } from "@/lib/ai/schemas";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { date } = body;

    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND", message: "No user found" } }, { status: 404 });

    const dateObj = new Date(date + "T00:00:00+08:00");
    const timeLog = await prisma.timeLog.findUnique({
      where: { userId_date: { userId: user.id, date: dateObj } },
      include: { entries: { include: { skill: true } } },
    });

    if (!timeLog || timeLog.entries.length === 0) {
      return NextResponse.json({ ok: false, error: { code: "NOT_FOUND", message: "No entries for this date" } }, { status: 404 });
    }

    // Get 7-day average
    const sevenDaysAgo = new Date(dateObj);
    sevenDaysAgo.setDate(sevenDaysAgo.getDate() - 7);
    const recentLogs = await prisma.timeLog.findMany({
      where: { userId: user.id, date: { gte: sevenDaysAgo, lt: dateObj }, status: { notIn: ["pending"] } },
      select: { totalEffectiveHours: true },
    });
    const avg7d = recentLogs.length > 0
      ? recentLogs.reduce((s, l) => s + (l.totalEffectiveHours ?? 0), 0) / recentLogs.length
      : 0;

    const activeCommitments = await prisma.commitment.findMany({
      where: { userId: user.id, status: "active" },
      select: { id: true, title: true, targetHours: true, actualHours: true, linkedSkillId: true },
    });

    const entriesJson = timeLog.entries.map((e) => ({
      activity: e.activity,
      category: e.category,
      raw_hours: e.rawHours,
      effective_hours: e.effectiveHours,
      quality_score: e.qualityScore,
      deliberate: e.deliberate,
      skill: e.skill?.name,
    }));

    const prompt = `${DAILY_ANALYSIS_PROMPT}

# 输出 JSON

{
  "verdict": "string (≤80字)",
  "risk": "string (≤80字)",
  "order": "string (≤80字)",
  "key_findings": ["string", ...],
  "tomorrow_focus": {
    "skill_id": "string | null",
    "suggested_hours": number,
    "concrete_action": "string"
  }
}

# 输入数据

- 日期：${date}
- 今日记录：${JSON.stringify(entriesJson)}
- 总原始小时：${timeLog.totalRawHours}
- 总有效小时：${timeLog.totalEffectiveHours}
- 转化率：${timeLog.conversionRate}
- 近 7 天均值有效小时：${avg7d.toFixed(1)}
- 当前活跃承诺：${JSON.stringify(activeCommitments)}`;

    const anthropic = getAnthropic();
    const response = await anthropic.messages.create({
      model: getModel(),
      max_tokens: 1500,
      messages: [{ role: "user", content: prompt }],
    });

    const text = response.content[0].type === "text" ? response.content[0].text : "";
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI did not return valid JSON" } }, { status: 500 });
    }

    const parsed = DailyAnalysisResponseSchema.safeParse(JSON.parse(jsonMatch[0]));
    if (!parsed.success) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI output validation failed" } }, { status: 500 });
    }

    // Save report
    await prisma.report.upsert({
      where: { userId_type_scopeKey: { userId: user.id, type: "daily", scopeKey: date } },
      create: {
        userId: user.id,
        type: "daily",
        scopeKey: date,
        inputSummary: JSON.stringify({ entries: entriesJson, totals: { raw: timeLog.totalRawHours, effective: timeLog.totalEffectiveHours } }),
        content: JSON.stringify(parsed.data),
        keyFindings: JSON.stringify(parsed.data.key_findings),
        problems: JSON.stringify([]),
        recommendations: JSON.stringify([parsed.data.order]),
        riskWarnings: JSON.stringify([parsed.data.risk]),
      },
      update: {
        content: JSON.stringify(parsed.data),
        keyFindings: JSON.stringify(parsed.data.key_findings),
        recommendations: JSON.stringify([parsed.data.order]),
        riskWarnings: JSON.stringify([parsed.data.risk]),
        generatedAt: new Date(),
      },
    });

    return NextResponse.json({ ok: true, data: parsed.data });
  } catch (error) {
    console.error("daily-analysis error:", error);
    return NextResponse.json({ ok: false, error: { code: "INTERNAL", message: "Internal server error" } }, { status: 500 });
  }
}
