import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { computeCreditDelta, applyCreditEvent, creditTier } from "@/lib/credit/calculate";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const { id } = await params;
    const body = await req.json().catch(() => ({}));
    const retroNote = body.retroNote ?? "";

    const user = await prisma.user.findFirst();
    if (!user) return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });

    const commitment = await prisma.commitment.findUnique({ where: { id } });
    if (!commitment || commitment.userId !== user.id) {
      return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });
    }

    if (commitment.status !== "active") {
      return NextResponse.json({ ok: false, error: { code: "CONSTRAINT", message: "Commitment already settled" } }, { status: 409 });
    }

    const now = new Date();
    const onTime = now <= commitment.deadline;
    const completionRate = commitment.targetHours > 0
      ? Math.min(1, commitment.actualHours / commitment.targetHours)
      : 0;

    let status: string;
    if (completionRate >= 1 && onTime) status = "completed";
    else if (completionRate >= 1 && !onTime) status = "late_completed";
    else if (completionRate >= 0.5) status = "partial";
    else status = "failed";

    const estimateAccuracy = commitment.targetHours > 0
      ? Math.abs(commitment.actualHours - commitment.targetHours) / commitment.targetHours
      : 0;

    const delta = computeCreditDelta({
      status: status as "completed" | "late_completed" | "partial" | "failed" | "abandoned" | "adjusted",
      completionRate,
      onTime,
      difficulty: commitment.difficulty,
      hasRetrospection: !!retroNote,
      estimateAccuracy,
    });

    const newScore = applyCreditEvent(user.creditScore, delta);
    const newTier = creditTier(newScore);

    const result = await prisma.$transaction(async (tx) => {
      const updated = await tx.commitment.update({
        where: { id },
        data: {
          status,
          completionRate,
          onTime,
          retroNote: retroNote || null,
          creditDelta: delta,
        },
      });

      await tx.user.update({
        where: { id: user.id },
        data: { creditScore: newScore, creditTier: newTier },
      });

      await tx.creditEvent.create({
        data: {
          userId: user.id,
          source: "commitment_settle",
          sourceId: id,
          delta,
          scoreBefore: user.creditScore,
          scoreAfter: newScore,
          reason: `${commitment.title}: ${status} (${(completionRate * 100).toFixed(0)}%)`,
        },
      });

      return updated;
    });

    return NextResponse.json({
      ok: true,
      data: {
        commitment: result,
        credit: { before: user.creditScore, after: newScore, delta, tier: newTier },
      },
    });
  } catch (error) {
    console.error("settle error:", error);
    return NextResponse.json({ ok: false, error: { code: "INTERNAL" } }, { status: 500 });
  }
}
