"use client";

import { cn } from "@/lib/utils";

interface UtilizationBarProps {
  segments: { label: string; value: number; color: string }[];
  total: number;
  className?: string;
}

export function UtilizationBar({ segments, total, className }: UtilizationBarProps) {
  if (total === 0) return null;
  return (
    <div className={cn("space-y-1", className)}>
      <div className="flex h-3 w-full overflow-hidden rounded-full">
        {segments.map((seg, i) => {
          const pct = (seg.value / total) * 100;
          if (pct < 0.5) return null;
          return (
            <div
              key={i}
              className="h-full transition-all"
              style={{ width: `${pct}%`, backgroundColor: seg.color }}
              title={`${seg.label}: ${seg.value.toFixed(1)}h (${pct.toFixed(0)}%)`}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {segments.map((seg, i) => (
          <div key={i} className="flex items-center gap-1">
            <div className="w-2 h-2 rounded-full" style={{ backgroundColor: seg.color }} />
            <span>{seg.label}: {seg.value.toFixed(1)}h</span>
          </div>
        ))}
      </div>
    </div>
  );
}
