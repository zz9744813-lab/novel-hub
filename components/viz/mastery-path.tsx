"use client";

import { cn } from "@/lib/utils";

interface MasteryPathProps {
  levels: { level: number; levelName: string }[];
  current: number;
  className?: string;
}

export function MasteryPath({ levels, current, className }: MasteryPathProps) {
  return (
    <div className={cn("flex items-center gap-0 w-full", className)}>
      {levels.map((lv, i) => {
        const isActive = lv.level === current;
        const isPast = lv.level < current;
        return (
          <div key={lv.level} className="flex items-center flex-1">
            <div className="flex flex-col items-center gap-1">
              <div
                className={cn(
                  "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-all",
                  isActive && "border-yellow-500 bg-yellow-500/20 text-yellow-400 ring-2 ring-yellow-500/30",
                  isPast && "border-green-500 bg-green-500 text-background",
                  !isActive && !isPast && "border-muted bg-muted/30 text-muted-foreground"
                )}
              >
                {lv.level}
              </div>
              <span className={cn("text-[10px]", isActive ? "text-yellow-400 font-semibold" : "text-muted-foreground")}>
                {lv.levelName}
              </span>
            </div>
            {i < levels.length - 1 && (
              <div className={cn("flex-1 h-0.5 mx-1", isPast ? "bg-green-500" : "bg-muted")} />
            )}
          </div>
        );
      })}
    </div>
  );
}
