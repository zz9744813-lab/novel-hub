"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/viz/stat-card";
import { CreditGauge } from "@/components/viz/credit-gauge";
import { UtilizationBar } from "@/components/viz/utilization-bar";
import { SkillProgress } from "@/components/viz/skill-progress";
import { MasteryPath } from "@/components/viz/mastery-path";
import { DiagBlock } from "@/components/viz/diag-block";
import { toast } from "sonner";

const CATEGORY_COLORS: Record<string, string> = {
  creation: "#22c55e",
  learning: "#3b82f6",
  skill_practice: "#a855f7",
  body: "#f97316",
  project: "#06b6d4",
  leisure: "#6b7280",
  drift: "#ef4444",
  other: "#9ca3af",
};

export default function Dashboard() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [rawInput, setRawInput] = useState("");
  const [parsing, setParsing] = useState(false);

  const fetchDashboard = useCallback(async () => {
    try {
      const res = await fetch("/api/dashboard");
      const json = await res.json();
      if (json.ok) setData(json.data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchDashboard(); }, [fetchDashboard]);

  const handleParse = async () => {
    if (!rawInput.trim()) return;
    setParsing(true);
    try {
      const today = new Date().toISOString().split("T")[0];
      const res = await fetch("/api/ai/parse-log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date: today, rawInput }),
      });
      const json = await res.json();
      if (json.ok) {
        toast.success("Parsed successfully");
        setRawInput("");
        fetchDashboard();
      } else {
        toast.error(json.error?.message ?? "Parse failed");
      }
    } catch {
      toast.error("Network error");
    } finally {
      setParsing(false);
    }
  };

  if (loading) {
    return <div className="p-8 text-muted-foreground">Loading...</div>;
  }

  if (!data) {
    return (
      <div className="p-8 max-w-2xl mx-auto">
        <Card>
          <CardHeader>
            <CardTitle>Welcome to Evolution OS</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4">Tell me what you did today to get started.</p>
            <Textarea
              placeholder="Today I..."
              value={rawInput}
              onChange={(e) => setRawInput(e.target.value)}
              rows={4}
            />
            <Button className="mt-2" onClick={handleParse} disabled={parsing}>
              {parsing ? "Parsing..." : "Parse"}
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const { user, today, skills, activeCommitments, latestDailyReport, weekStats } = data;
  const entries = today?.entries ?? [];
  const totals = today ? { raw: today.totalRawHours, effective: today.totalEffectiveHours, conversion: today.conversionRate } : { raw: 0, effective: 0, conversion: 0 };

  const segments = entries.map((e: any) => ({
    label: e.activity,
    value: e.rawHours,
    color: CATEGORY_COLORS[e.category] ?? "#9ca3af",
  }));

  let diagData: any = null;
  if (latestDailyReport?.content) {
    try { diagData = JSON.parse(latestDailyReport.content); } catch {}
  }

  const defaultLevels = [
    { level: 0, levelName: "未入门" }, { level: 1, levelName: "入门" }, { level: 2, levelName: "初级" },
    { level: 3, levelName: "合格" }, { level: 4, levelName: "熟练" }, { level: 5, levelName: "专业" },
    { level: 6, levelName: "专家" }, { level: 7, levelName: "大师候选" }, { level: 8, levelName: "大师" },
  ];

  const primarySkill = skills?.[0];

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      {/* Hero */}
      <div className="flex items-start justify-between gap-6">
        <div className="flex-1">
          <h1 className="text-2xl font-bold mb-1">
            {new Date().toLocaleDateString("zh-CN", { weekday: "long", month: "long", day: "numeric" })}
          </h1>
          <Textarea
            placeholder="Today I..."
            value={rawInput}
            onChange={(e) => setRawInput(e.target.value)}
            rows={3}
            className="mt-2"
          />
          <Button className="mt-2" onClick={handleParse} disabled={parsing} size="sm">
            {parsing ? "Parsing..." : "Parse"}
          </Button>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <StatCard label="Raw" value={totals.raw?.toFixed(1) ?? "0"} suffix="h" variant={totals.raw >= 8 ? "good" : "default"} />
          <StatCard label="Effective" value={totals.effective?.toFixed(1) ?? "0"} suffix="h" variant={totals.effective >= 5 ? "good" : "default"} />
          <StatCard label="Credit" value={user.creditScore.toFixed(0)} variant={user.creditTier === "S" ? "gold" : user.creditTier === "D" ? "warn" : "default"} />
          <StatCard label="7d Conv" value={`${((weekStats.conversionRate ?? 0) * 100).toFixed(0)}%`} variant={weekStats.conversionRate >= 0.7 ? "good" : weekStats.conversionRate < 0.5 ? "warn" : "default"} />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Ledger */}
        <Card className="col-span-2">
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wide">{"\u00A7"}I The Day&apos;s Ledger</CardTitle>
          </CardHeader>
          <CardContent>
            {entries.length === 0 ? (
              <p className="text-muted-foreground text-sm">Awaiting today&apos;s entry</p>
            ) : (
              <>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-muted-foreground text-xs">
                      <th className="text-left py-1">#</th>
                      <th className="text-left">Activity</th>
                      <th className="text-left">Category</th>
                      <th className="text-right">Raw</th>
                      <th className="text-right">Eff%</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map((e: any, i: number) => (
                      <tr key={e.id} className="border-t border-border">
                        <td className="py-1.5 text-muted-foreground">{i + 1}</td>
                        <td>{e.activity}</td>
                        <td><Badge variant="outline" className="text-xs">{e.category}</Badge></td>
                        <td className="text-right">{e.rawHours.toFixed(1)}h</td>
                        <td className="text-right">{e.rawHours > 0 ? ((e.effectiveHours / e.rawHours) * 100).toFixed(0) : 0}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="mt-3">
                  <UtilizationBar segments={segments} total={totals.raw ?? 0} />
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Credit + Commitments */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm uppercase tracking-wide">Credit</CardTitle>
            </CardHeader>
            <CardContent className="flex justify-center">
              <CreditGauge score={user.creditScore} tier={user.creditTier} />
            </CardContent>
          </Card>

          {activeCommitments.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm uppercase tracking-wide">Active Commitments</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {activeCommitments.map((c: any) => (
                  <div key={c.id} className="flex items-center justify-between text-sm">
                    <span className="truncate flex-1">{c.title}</span>
                    <span className="text-muted-foreground ml-2">{c.actualHours}/{c.targetHours}h</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      {/* Skills */}
      {skills && skills.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wide">{"\u00A7"}II The Craft in Progress</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-3 gap-4">
              {skills.slice(0, 3).map((s: any, i: number) => {
                const lvName = s.levels?.find((l: any) => l.level === s.currentLevel)?.levelName ?? "";
                const nextLv = s.levels?.find((l: any) => l.level === s.currentLevel + 1);
                const hoursToNext = nextLv ? Math.max(0, nextLv.minEffectiveHours - s.effectiveHoursTotal) : 0;
                return (
                  <SkillProgress
                    key={s.id}
                    name={s.name}
                    level={s.currentLevel}
                    levelName={lvName}
                    effectiveHours={s.effectiveHoursTotal}
                    hoursToNext={hoursToNext}
                    description={s.description ?? undefined}
                    featured={i === 0}
                  />
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Mastery Path */}
      {primarySkill && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wide">{"\u00A7"}III The Master&apos;s Path — {primarySkill.name}</CardTitle>
          </CardHeader>
          <CardContent>
            <MasteryPath levels={defaultLevels} current={primarySkill.currentLevel} />
          </CardContent>
        </Card>
      )}

      {/* Diagnostics */}
      <div className="grid grid-cols-2 gap-6">
        {diagData && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm uppercase tracking-wide">{"\u00A7"}IV Editor&apos;s Notes</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {diagData.verdict && <DiagBlock type="verdict" text={diagData.verdict} />}
              {diagData.risk && <DiagBlock type="risk" text={diagData.risk} />}
              {diagData.order && <DiagBlock type="order" text={diagData.order} />}
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wide">{"\u00A7"}V Build Order</CardTitle>
          </CardHeader>
          <CardContent>
            {activeCommitments.length > 0 ? (
              <div className="space-y-2">
                {activeCommitments.map((c: any) => (
                  <div key={c.id} className="flex items-center gap-2">
                    <span className="text-muted-foreground">{"\u25A1"}</span>
                    <span className="flex-1 text-sm">{c.title}</span>
                    <Badge variant={c.status === "active" ? "default" : "secondary"}>{c.status}</Badge>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">No active commitments. Set one in Commitments.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
