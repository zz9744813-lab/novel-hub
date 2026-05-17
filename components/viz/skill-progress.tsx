"use client";

import { cn } from "@/lib/utils";

interface SkillProgressProps {
  name: string;
  level: number;
  levelName: string;
  effectiveHours: number;
  hoursToNext: number;
  description?: string;
  featured?: boolean;
  className?: string;
}

export function SkillProgress({ name, level, levelName, effectiveHours, hoursToNext, description, featured, className }: SkillProgressProps) {
  const pct = hoursToNext > 0 ? Math.max(0, Math.min(100, ((effectiveHours) / (effectiveHours + hoursToNext)) * 100)) : 100;

  return (
    <div className={cn("rounded-lg border p-4 space-y-2", featured && "border-yellow-500/50 bg-yellow-500/5", className)}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {featured && <span className="text-yellow-500 text-xs font-bold">FEATURED</span>}
          <span className="font-semibold">{name}</span>
        </div>
        <span className="text-xs bg-secondary px-2 py-0.5 rounded-full">L{level} {levelName}</span>
      </div>
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <div className="space-y-1">
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>{effectiveHours.toFixed(1)}h</span>
          <span>{hoursToNext > 0 ? `${hoursToNext.toFixed(0)}h to L${level + 1}` : "Max level"}</span>
        </div>
        <div className="h-2 bg-muted rounded-full overflow-hidden">
          <div
            className={cn("h-full rounded-full transition-all", featured ? "bg-yellow-500" : "bg-primary")}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </div>
  );
}
