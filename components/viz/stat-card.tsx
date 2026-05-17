"use client";

import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string | number;
  suffix?: string;
  variant?: "default" | "good" | "gold" | "warn";
  className?: string;
}

export function StatCard({ label, value, suffix, variant = "default", className }: StatCardProps) {
  const borderColors = {
    default: "border-l-muted-foreground",
    good: "border-l-green-500",
    gold: "border-l-yellow-500",
    warn: "border-l-red-500",
  };
  return (
    <div className={cn("border-l-4 pl-3 py-1", borderColors[variant], className)}>
      <div className="text-xs text-muted-foreground uppercase tracking-wide">{label}</div>
      <div className="text-2xl font-bold">
        {value}
        {suffix && <span className="text-sm font-normal text-muted-foreground ml-1">{suffix}</span>}
      </div>
    </div>
  );
}
