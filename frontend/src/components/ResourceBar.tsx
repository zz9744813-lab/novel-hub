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
    <div className="flex items-center gap-4 text-2xs font-mono">
      <div className="flex items-center gap-1.5">
        <div className={clsx("w-1.5 h-1.5 rounded-full", res.resource_safe ? "bg-success" : "bg-danger")} />
        <span className="text-text-secondary" style={{ fontWeight: 510 }}>{res.resource_safe ? "OK" : "WARN"}</span>
      </div>
      <span className="text-text-disabled">
        RAM <span className="text-text-secondary">{res.available_mb}</span>M
      </span>
      <span className="text-text-disabled">
        SWP <span className="text-text-secondary">{res.swap_used_pct}</span>%
      </span>
    </div>
  );
}
