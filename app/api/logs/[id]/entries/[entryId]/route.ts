import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/db";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; entryId: string }> }
) {
  try {
    const { id, entryId } = await params;
    const body = await req.json();

    const entry = await prisma.logEntry.findUnique({ where: { id: entryId } });
    if (!entry || entry.timeLogId !== id) {
      return NextResponse.json({ ok: false, error: { code: "NOT_FOUND" } }, { status: 404 });
    }

    const updated = await prisma.logEntry.update({
      where: { id: entryId },
      data: {
        ...(body.activity !== undefined && { activity: body.activity }),
        ...(body.category !== undefined && { category: body.category }),
        ...(body.rawHours !== undefined && { rawHours: body.rawHours }),
        ...(body.effectiveHours !== undefined && { effectiveHours: body.effectiveHours }),
        ...(body.qualityScore !== undefined && { qualityScore: body.qualityScore }),
        ...(body.skillId !== undefined && { skillId: body.skillId }),
        source: "edited",
      },
    });

    return NextResponse.json({ ok: true, data: updated });
  } catch (error) {
    return NextResponse.json({ ok: false, error: { code: "INTERNAL" } }, { status: 500 });
  }
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string; entryId: string }> }
) {
  try {
    const { entryId } = await params;
    await prisma.logEntry.delete({ where: { id: entryId } });
    return NextResponse.json({ ok: true });
  } catch (error) {
    return NextResponse.json({ ok: false, error: { code: "INTERNAL" } }, { status: 500 });
  }
}
