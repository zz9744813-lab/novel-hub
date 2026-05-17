"use client";

import { cn } from "@/lib/utils";

interface HeatmapProps {
  days: number;
  valuesByDate: Record<string, number>;
  className?: string;
}

export function Heatmap({ days, valuesByDate, className }: HeatmapProps) {
  const cells: { date: string; value: number; label: string }[] = [];
  const now = new Date();

  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().split("T")[0];
    cells.push({
      date: key,
      value: valuesByDate[key] ?? 0,
      label: key,
    });
  }

  const maxVal = Math.max(1, ...cells.map((c) => c.value));

  function getColor(v: number): string {
    if (v === 0) return "bg-muted";
    const ratio = v / maxVal;
    if (ratio < 0.25) return "bg-green-900";
    if (ratio < 0.5) return "bg-green-700";
    if (ratio < 0.75) return "bg-green-500";
    return "bg-green-400";
  }

  return (
    <div className={cn("flex flex-wrap gap-0.5", className)}>
      {cells.map((cell) => (
        <div
          key={cell.date}
          className={cn("w-3 h-3 rounded-sm", getColor(cell.value))}
          title={`${cell.label}: ${cell.value.toFixed(1)}h`}
        />
      ))}
    </div>
  );
}
