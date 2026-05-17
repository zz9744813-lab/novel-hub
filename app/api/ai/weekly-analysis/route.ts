import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getAnthropic, getModel } from "@/lib/ai/client";
import { WEEKLY_ANALYSIS_PROMPT } from "@/lib/ai/prompts";
import { WeeklyAnalysisResponseSchema } from "@/lib/ai/schemas";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { weekStart, weekEnd } = body;

    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND", message: "No user found" } }, { status: 404 });

    const startDate = new Date(weekStart + "T00:00:00+08:00");
    const endDate = new Date(weekEnd + "T00:00:00+08:00");

    const logs = await prisma.timeLog.findMany({
      where: { userId: user.id, date: { gte: startDate, lt: endDate }, status: { notIn: ["pending"] } },
      include: { entries: { include: { skill: true } } },
    });

    const raw = logs.reduce((s, l) => s + (l.totalRawHours ?? 0), 0);
    const effective = logs.reduce((s, l) => s + (l.totalEffectiveHours ?? 0), 0);
    const conv = raw > 0 ? (effective / raw).toFixed(2) : "0";

    // Last week comparison
    const lastWeekStart = new Date(startDate);
    lastWeekStart.setDate(lastWeekStart.getDate() - 7);
    const lastWeekLogs = await prisma.timeLog.findMany({
      where: { userId: user.id, date: { gte: lastWeekStart, lt: startDate }, status: { notIn: ["pending"] } },
      select: { totalRawHours: true, totalEffectiveHours: true },
    });
    const lastRaw = lastWeekLogs.reduce((s, l) => s + (l.totalRawHours ?? 0), 0);
    const lastEffective = lastWeekLogs.reduce((s, l) => s + (l.totalEffectiveHours ?? 0), 0);
    const lastConv = lastRaw > 0 ? (lastEffective / lastRaw).toFixed(2) : "0";

    const settledCommitments = await prisma.commitment.findMany({
      where: { userId: user.id, status: { in: ["completed", "late_completed", "partial", "failed"] }, deadline: { gte: startDate, lt: endDate } },
      select: { title: true, status: true, completionRate: true, creditDelta: true },
    });

    const activeCommitments = await prisma.commitment.findMany({
      where: { userId: user.id, status: "active" },
      select: { title: true, targetHours: true, actualHours: true, deadline: true },
    });

    const skillDist = new Map<string, { raw: number; effective: number }>();
    for (const log of logs) {
      for (const entry of log.entries) {
        if (entry.skill) {
          const d = skillDist.get(entry.skill.name) ?? { raw: 0, effective: 0 };
          d.raw += entry.rawHours;
          d.effective += entry.effectiveHours;
          skillDist.set(entry.skill.name, d);
        }
      }
    }

    const prompt = `${WEEKLY_ANALYSIS_PROMPT}

# 输出 JSON

{
  "content": "string (Markdown)",
  "key_findings": ["string", ...],
  "problems": ["string", ...],
  "recommendations": ["string", ...],
  "risk_warnings": ["string", ...]
}

# 输入

- 周区间：${weekStart} → ${weekEnd}
- 总原始小时：${raw.toFixed(1)}
- 总有效小时：${effective.toFixed(1)}
- 转化率本周 vs 上周：${conv} / ${lastConv}
- 各技能本周 effective 小时分布：${JSON.stringify(Object.fromEntries(skillDist))}
- 本周承诺结算：${JSON.stringify(settledCommitments)}
- 当前活跃承诺：${JSON.stringify(activeCommitments)}
- 信用分：${user.creditScore}（${user.creditTier}）`;

    const anthropic = getAnthropic();
    const response = await anthropic.messages.create({
      model: getModel(),
      max_tokens: 3000,
      messages: [{ role: "user", content: prompt }],
    });

    const text = response.content[0].type === "text" ? response.content[0].text : "";
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI did not return valid JSON" } }, { status: 500 });
    }

    const parsed = WeeklyAnalysisResponseSchema.safeParse(JSON.parse(jsonMatch[0]));
    if (!parsed.success) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI output validation failed" } }, { status: 500 });
    }

    const scopeKey = `${startDate.getFullYear()}-W${String(Math.ceil(startDate.getDate() / 7)).padStart(2, "0")}`;

    await prisma.report.upsert({
      where: { userId_type_scopeKey: { userId: user.id, type: "weekly", scopeKey } },
      create: {
        userId: user.id,
        type: "weekly",
        scopeKey,
        inputSummary: JSON.stringify({ raw, effective, conv }),
        content: parsed.data.content,
        keyFindings: JSON.stringify(parsed.data.key_findings),
        problems: JSON.stringify(parsed.data.problems),
        recommendations: JSON.stringify(parsed.data.recommendations),
        riskWarnings: JSON.stringify(parsed.data.risk_warnings),
      },
      update: {
        content: parsed.data.content,
        keyFindings: JSON.stringify(parsed.data.key_findings),
        problems: JSON.stringify(parsed.data.problems),
        recommendations: JSON.stringify(parsed.data.recommendations),
        riskWarnings: JSON.stringify(parsed.data.risk_warnings),
        generatedAt: new Date(),
      },
    });

    return NextResponse.json({ ok: true, data: parsed.data });
  } catch (error) {
    console.error("weekly-analysis error:", error);
    return NextResponse.json({ ok: false, error: { code: "INTERNAL", message: "Internal server error" } }, { status: 500 });
  }
}
