"use client";

import { cn } from "@/lib/utils";

interface CreditGaugeProps {
  score: number;
  tier: "S" | "A" | "B" | "C" | "D";
  className?: string;
}

const tierColors: Record<string, string> = {
  S: "text-yellow-400",
  A: "text-green-400",
  B: "text-blue-400",
  C: "text-orange-400",
  D: "text-red-400",
};

export function CreditGauge({ score, tier, className }: CreditGaugeProps) {
  const pct = Math.min(100, Math.max(0, ((score - 40) / 110) * 100));
  const circumference = 2 * Math.PI * 45;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className={cn("flex flex-col items-center gap-1", className)}>
      <svg width="100" height="100" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="45" fill="none" stroke="hsl(var(--muted))" strokeWidth="8" />
        <circle
          cx="50" cy="50" r="45" fill="none"
          stroke="currentColor"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className={cn("transition-all duration-700", tierColors[tier])}
          transform="rotate(-90 50 50)"
        />
        <text x="50" y="46" textAnchor="middle" className="text-2xl font-bold fill-foreground">{score.toFixed(0)}</text>
        <text x="50" y="62" textAnchor="middle" className={cn("text-sm font-semibold", tierColors[tier])}>{tier}</text>
      </svg>
    </div>
  );
}
