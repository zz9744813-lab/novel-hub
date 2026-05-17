"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { UtilizationBar } from "@/components/viz/utilization-bar";
import { toast } from "sonner";

const CATEGORY_COLORS: Record<string, string> = {
  creation: "#22c55e", learning: "#3b82f6", skill_practice: "#a855f7",
  body: "#f97316", project: "#06b6d4", leisure: "#6b7280", drift: "#ef4444", other: "#9ca3af",
};

export default function LogPage() {
  const [logs, setLogs] = useState<any[]>([]);
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().split("T")[0]);
  const [todayLog, setTodayLog] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const fetchLogs = useCallback(async () => {
    try {
      const res = await fetch("/api/logs");
      const json = await res.json();
      if (json.ok) setLogs(json.data);
    } catch {}
  }, []);

  const fetchDay = useCallback(async (date: string) => {
    try {
      const res = await fetch(`/api/logs?date=${date}`);
      const json = await res.json();
      if (json.ok) setTodayLog(json.data);
      else setTodayLog(null);
    } catch {
      setTodayLog(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);
  useEffect(() => { fetchDay(selectedDate); }, [selectedDate, fetchDay]);

  const entries = todayLog?.entries ?? [];
  const segments = entries.map((e: any) => ({
    label: e.activity, value: e.rawHours, color: CATEGORY_COLORS[e.category] ?? "#9ca3af",
  }));

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold">Ledger</h1>

      <div className="flex items-center gap-4">
        <input
          type="date"
          value={selectedDate}
          onChange={(e) => setSelectedDate(e.target.value)}
          className="bg-background border border-input rounded-md px-3 py-1.5 text-sm"
        />
        <span className="text-muted-foreground text-sm">
          {logs.length} records tracked
        </span>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wide">{selectedDate}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-muted-foreground">Loading...</p>
          ) : entries.length === 0 ? (
            <p className="text-muted-foreground">No entries for this date</p>
          ) : (
            <>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-muted-foreground text-xs">
                    <th className="text-left py-1">#</th>
                    <th className="text-left">Activity</th>
                    <th className="text-left">Category</th>
                    <th className="text-right">Raw</th>
                    <th className="text-right">Effective</th>
                    <th className="text-right">Quality</th>
                    <th className="text-left">AI Note</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e: any, i: number) => (
                    <tr key={e.id} className="border-t border-border">
                      <td className="py-1.5 text-muted-foreground">{i + 1}</td>
                      <td>{e.activity}</td>
                      <td><Badge variant="outline" className="text-xs">{e.category}</Badge></td>
                      <td className="text-right">{e.rawHours.toFixed(1)}h</td>
                      <td className="text-right">{e.effectiveHours.toFixed(1)}h</td>
                      <td className="text-right">{e.qualityScore}/10</td>
                      <td className="text-xs text-muted-foreground max-w-[200px] truncate">{e.aiReason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-3">
                <UtilizationBar segments={segments} total={todayLog.totalRawHours ?? 0} />
              </div>
              {todayLog.aiOverallNote && (
                <p className="mt-2 text-sm text-muted-foreground italic">{todayLog.aiOverallNote}</p>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
