import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getAnthropic, getModel } from "@/lib/ai/client";
import { REALITY_CHECK_PROMPT } from "@/lib/ai/prompts";
import { RealityCheckResponseSchema } from "@/lib/ai/schemas";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { title, type, startDate, deadline, targetHours, completionStandard, difficulty, linkedSkillId } = body;

    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND", message: "No user found" } }, { status: 404 });

    // Gather context
    const activeCommitments = await prisma.commitment.findMany({
      where: { userId: user.id, status: "active" },
      select: { title: true, targetHours: true, deadline: true, actualHours: true },
    });

    const category = linkedSkillId
      ? (await prisma.skill.findUnique({ where: { id: linkedSkillId }, select: { category: true } }))?.category
      : null;

    const fourWeeksAgo = new Date();
    fourWeeksAgo.setDate(fourWeeksAgo.getDate() - 28);
    const recentLogs = await prisma.logEntry.findMany({
      where: {
        timeLog: { userId: user.id, date: { gte: fourWeeksAgo } },
        ...(category ? { skill: { category } } : {}),
      },
      select: { rawHours: true },
    });
    const recentHoursTotal = recentLogs.reduce((sum, e) => sum + e.rawHours, 0);

    const prompt = `${REALITY_CHECK_PROMPT}

# 输出 JSON

{
  "verdict": "pass" | "warn" | "block",
  "difficulty_ai": 1-5,
  "reasoning": "string (≤80字)",
  "suggested_target_hours": number | null,
  "suggested_deadline": "ISO date | null",
  "risk_factors": ["string", ...]
}

# 上下文

- 用户：${user.name}
- 当前信用分：${user.creditScore}（${user.creditTier}）
- 近 4 周该类活动小时数：${recentHoursTotal.toFixed(1)}
- 当前活跃承诺：${JSON.stringify(activeCommitments)}

# 用户提交的承诺

- 标题：${title}
- 类型：${type}
- 开始：${startDate}
- 截止：${deadline}
- 目标小时：${targetHours}
- 自评难度：${difficulty}
- 完成标准：${completionStandard}`;

    const anthropic = getAnthropic();
    const response = await anthropic.messages.create({
      model: getModel(),
      max_tokens: 1000,
      messages: [{ role: "user", content: prompt }],
    });

    const text = response.content[0].type === "text" ? response.content[0].text : "";
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI did not return valid JSON" } }, { status: 500 });
    }

    const parsed = RealityCheckResponseSchema.safeParse(JSON.parse(jsonMatch[0]));
    if (!parsed.success) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI output validation failed" } }, { status: 500 });
    }

    return NextResponse.json({ ok: true, data: parsed.data });
  } catch (error) {
    console.error("reality-check error:", error);
    return NextResponse.json({ ok: false, error: { code: "INTERNAL", message: "Internal server error" } }, { status: 500 });
  }
}
