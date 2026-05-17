import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getAnthropic, getModel } from "@/lib/ai/client";
import { SKILL_ASSESSMENT_PROMPT } from "@/lib/ai/prompts";
import { SkillAssessmentResponseSchema } from "@/lib/ai/schemas";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { skillId } = body;

    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND", message: "No user found" } }, { status: 404 });

    const skill = await prisma.skill.findUnique({
      where: { id: skillId },
      include: { levels: { orderBy: { level: "asc" } } },
    });

    if (!skill || skill.userId !== user.id) {
      return NextResponse.json({ ok: false, error: { code: "NOT_FOUND", message: "Skill not found" } }, { status: 404 });
    }

    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
    const recentEntries = await prisma.logEntry.findMany({
      where: { skillId, timeLog: { userId: user.id, date: { gte: thirtyDaysAgo } } },
      select: { activity: true, rawHours: true, effectiveHours: true, qualityScore: true, deliberate: true, aiReason: true },
      orderBy: { timeLog: { date: "desc" } },
      take: 50,
    });

    const doneDeliverables: string[] = [];
    for (const lv of skill.levels) {
      try {
        const items = JSON.parse(lv.requiredDeliverables) as { text: string; done: boolean }[];
        for (const item of items) {
          if (item.done) doneDeliverables.push(`L${lv.level}: ${item.text}`);
        }
      } catch {}
    }

    const prompt = `${SKILL_ASSESSMENT_PROMPT}

# 输出 JSON

{
  "ai_assessed_level": 0-8,
  "agree_with_system": boolean,
  "disagreement_reason": "string | null",
  "strengths": ["string", ...],
  "weaknesses": ["string", ...],
  "next_level_deliverables": [
    { "text": "string", "verifiable_by": "string" }
  ],
  "next_3_actions": [
    { "title": "string", "estimated_hours": number, "rationale": "string" }
  ],
  "master_reference": "string | null"
}

# 输入

- 技能：${skill.name} / 分类：${skill.category}
- 累计原始小时：${skill.rawHoursTotal}
- 累计有效小时：${skill.effectiveHoursTotal}
- 刻意练习小时：${skill.deliberateHoursTotal}
- 当前等级：L${skill.currentLevel}
- 等级标准：${JSON.stringify(skill.levels)}
- 近 30 天记录：${JSON.stringify(recentEntries)}
- 已完成 deliverables：${JSON.stringify(doneDeliverables)}`;

    const anthropic = getAnthropic();
    const response = await anthropic.messages.create({
      model: getModel(),
      max_tokens: 2000,
      messages: [{ role: "user", content: prompt }],
    });

    const text = response.content[0].type === "text" ? response.content[0].text : "";
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI did not return valid JSON" } }, { status: 500 });
    }

    const parsed = SkillAssessmentResponseSchema.safeParse(JSON.parse(jsonMatch[0]));
    if (!parsed.success) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI output validation failed" } }, { status: 500 });
    }

    // Update skill with AI assessment
    await prisma.skill.update({
      where: { id: skillId },
      data: {
        lastAssessedAt: new Date(),
        strengths: JSON.stringify(parsed.data.strengths),
        weaknesses: JSON.stringify(parsed.data.weaknesses),
        aiComment: parsed.data.disagreement_reason,
      },
    });

    return NextResponse.json({ ok: true, data: parsed.data });
  } catch (error) {
    console.error("skill-assessment error:", error);
    return NextResponse.json({ ok: false, error: { code: "INTERNAL", message: "Internal server error" } }, { status: 500 });
  }
}
