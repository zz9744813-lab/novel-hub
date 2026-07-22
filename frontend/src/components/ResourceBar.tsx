import { useEffect, useState } from "react";
import { api } from "../api";
import clsx from "clsx";

export function ResourceBar() {
  const [res, setRes] = useState<{ available_mb: number; swap_used_pct: number; resource_safe: boolean } | null>(null);
  useEffect(() => {
    const p = () => api.resources().then(setRes).catch(() => {});
    p();
    const t = setInterval(p, 30000);
    return () => clearInterval(t);
  }, []);

  if (!res) return null;

  return (
    <div className="flex items-center gap-4 text-xs">
      <div className="flex items-center gap-1.5">
        <div className={clsx("w-2 h-2 rounded-full", res.resource_safe ? "bg-sage" : "bg-red-500")} />
        <span className="text-ink-300">{res.resource_safe ? "资源充足" : "资源告警"}</span>
      </div>
      <span className="text-ink-500 font-mono">
        RAM <span className="text-ink-300">{res.available_mb}</span>MB
      </span>
      <span className="text-ink-500 font-mono">
        Swap <span className="text-ink-300">{res.swap_used_pct}</span>%
      </span>
    </div>
  );
}