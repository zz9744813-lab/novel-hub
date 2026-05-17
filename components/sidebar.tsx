"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const navItems = [
  { href: "/", label: "Today", icon: "\u2600" },
  { href: "/log", label: "Ledger", icon: "\u270D" },
  { href: "/skills", label: "Skills", icon: "\u2B50" },
  { href: "/ladder", label: "Ladder", icon: "\u2191" },
  { href: "/commitments", label: "Commitments", icon: "\u2611" },
  { href: "/reports", label: "Reports", icon: "\u2693" },
  { href: "/settings", label: "Settings", icon: "\u2699" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-56 border-r border-border h-screen sticky top-0 flex flex-col">
      <div className="p-4 border-b border-border">
        <h1 className="text-lg font-bold tracking-tight">Evolution OS</h1>
        <p className="text-xs text-muted-foreground">Personal Growth Engine</p>
      </div>
      <nav className="flex-1 p-2 space-y-0.5">
        {navItems.map((item) => {
          const isActive = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 px-3 py-2 rounded-md text-sm transition-colors",
                isActive
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
              )}
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
