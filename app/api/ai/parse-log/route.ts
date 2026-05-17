import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getAnthropic, getModel } from "@/lib/ai/client";
import { PARSE_LOG_PROMPT } from "@/lib/ai/prompts";
import { ParseLogResponseSchema } from "@/lib/ai/schemas";
import { computeEffective, getBaseMultiplier } from "@/lib/time/effective";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { date, rawInput } = body;

    if (!date || !rawInput) {
      return NextResponse.json({ ok: false, error: { code: "VALIDATION_FAILED", message: "date and rawInput required" } }, { status: 400 });
    }

    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND", message: "No user found" } }, { status: 404 });

    const skills = await prisma.skill.findMany({
      where: { userId: user.id, isActive: true },
      select: { id: true, name: true, category: true },
    });

    const activeCommitments = await prisma.commitment.findMany({
      where: { userId: user.id, status: "active" },
      select: { title: true, linkedSkillId: true },
    });

    const prompt = `${PARSE_LOG_PROMPT}

# 输出格式

严格 JSON，结构如下：
{
  "entries": [
    {
      "activity": "string",
      "category": "creation|learning|skill_practice|body|project|leisure|drift|other",
      "raw_hours": number,
      "effective_hours": number,
      "quality_score": number,
      "deliberate_practice": boolean,
      "output_evidence": "string | null",
      "skill_id": "string | null",
      "suggest_new_skill": "string | null",
      "ai_reason": "string"
    }
  ],
  "untracked_hours": number,
  "overall_note": "string"
}

# 上下文

- 日期：${date}
- 用户活跃技能列表：${JSON.stringify(skills)}
- 当前活跃承诺：${JSON.stringify(activeCommitments)}

# 用户原始输入

"""
${rawInput}
"""`;

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

    const parsed = ParseLogResponseSchema.safeParse(JSON.parse(jsonMatch[0]));
    if (!parsed.success) {
      return NextResponse.json({ ok: false, error: { code: "AI_PARSE_FAILED", message: "AI output validation failed", details: parsed.error.flatten() } }, { status: 500 });
    }

    const data = parsed.data;
    const dateObj = new Date(date + "T00:00:00+08:00");

    // Recompute effective hours using our formula and take lower value
    const processedEntries = data.entries.map((entry) => {
      const recomputed = computeEffective(entry.raw_hours, {
        baseMultiplier: getBaseMultiplier(entry.category),
        hasOutput: !!entry.output_evidence,
        isDeliberate: entry.deliberate_practice,
        alignedWithCommitment: activeCommitments.some((c) => c.linkedSkillId === entry.skill_id),
        contextSwitchPenalty: 0,
        qualityScore: entry.quality_score,
      });
      return {
        ...entry,
        effective_hours: Math.min(entry.effective_hours, recomputed),
      };
    });

    const totalRaw = processedEntries.reduce((sum, e) => sum + e.raw_hours, 0);
    const totalEffective = processedEntries.reduce((sum, e) => sum + e.effective_hours, 0);

    // Upsert TimeLog + entries in transaction
    const timeLog = await prisma.$transaction(async (tx) => {
      const existing = await tx.timeLog.findUnique({
        where: { userId_date: { userId: user.id, date: dateObj } },
      });

      const logData = {
        userId: user.id,
        date: dateObj,
        rawInput,
        parsedAt: new Date(),
        status: "parsed" as const,
        totalRawHours: Number(totalRaw.toFixed(2)),
        totalEffectiveHours: Number(totalEffective.toFixed(2)),
        conversionRate: totalRaw > 0 ? Number((totalEffective / totalRaw).toFixed(2)) : 0,
        untrackedHours: data.untracked_hours,
        aiOverallNote: data.overall_note,
      };

      let log;
      if (existing) {
        await tx.logEntry.deleteMany({ where: { timeLogId: existing.id } });
        log = await tx.timeLog.update({ where: { id: existing.id }, data: logData });
      } else {
        log = await tx.timeLog.create({ data: logData });
      }

      for (const entry of processedEntries) {
        await tx.logEntry.create({
          data: {
            timeLogId: log.id,
            activity: entry.activity,
            category: entry.category,
            rawHours: entry.raw_hours,
            effectiveHours: entry.effective_hours,
            qualityScore: entry.quality_score,
            deliberate: entry.deliberate_practice,
            outputEvidence: entry.output_evidence,
            aiReason: entry.ai_reason,
            source: "ai",
            skillId: entry.skill_id,
          },
        });

        // Update skill totals
        if (entry.skill_id) {
          await tx.skill.update({
            where: { id: entry.skill_id },
            data: {
              rawHoursTotal: { increment: entry.raw_hours },
              effectiveHoursTotal: { increment: entry.effective_hours },
              ...(entry.deliberate_practice ? { deliberateHoursTotal: { increment: entry.effective_hours } } : {}),
            },
          });
        }
      }

      return log;
    });

    const entries = await prisma.logEntry.findMany({
      where: { timeLogId: timeLog.id },
      include: { skill: { select: { id: true, name: true } } },
    });

    return NextResponse.json({
      ok: true,
      data: {
        timeLogId: timeLog.id,
        entries: entries.map((e) => ({
          id: e.id,
          activity: e.activity,
          category: e.category,
          rawHours: e.rawHours,
          effectiveHours: e.effectiveHours,
          qualityScore: e.qualityScore,
          deliberate: e.deliberate,
          skillId: e.skillId,
          skillName: e.skill?.name ?? null,
          outputEvidence: e.outputEvidence,
          aiReason: e.aiReason,
        })),
        totals: {
          raw: timeLog.totalRawHours,
          effective: timeLog.totalEffectiveHours,
          conversion: timeLog.conversionRate,
        },
        untrackedHours: timeLog.untrackedHours,
        suggestedNewSkills: data.entries.filter((e) => e.suggest_new_skill).map((e) => e.suggest_new_skill),
        overallNote: timeLog.aiOverallNote,
      },
    });
  } catch (error) {
    console.error("parse-log error:", error);
    return NextResponse.json({ ok: false, error: { code: "INTERNAL", message: "Internal server error" } }, { status: 500 });
  }
}
