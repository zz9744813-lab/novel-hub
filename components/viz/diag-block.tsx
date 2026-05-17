"use client";

import { cn } from "@/lib/utils";

interface DiagBlockProps {
  type: "verdict" | "risk" | "order";
  text: string;
  className?: string;
}

const icons = {
  verdict: "\u25B6",
  risk: "\u26A0",
  order: "\u2192",
};

const colors = {
  verdict: "border-l-yellow-500 text-yellow-400",
  risk: "border-l-red-500 text-red-400",
  order: "border-l-blue-500 text-blue-400",
};

export function DiagBlock({ type, text, className }: DiagBlockProps) {
  const labels = { verdict: "\u5224", risk: "\u9669", order: "\u4EE4" };
  return (
    <div className={cn("border-l-4 pl-3 py-2", colors[type], className)}>
      <div className="text-xs font-semibold uppercase tracking-wide mb-1">
        {icons[type]} {labels[type]}
      </div>
      <div className="text-sm">{text}</div>
    </div>
  );
}
