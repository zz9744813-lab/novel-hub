"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export default function SettingsPage() {
  const [user, setUser] = useState<any>(null);
  const [creditHistory, setCreditHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch("/api/dashboard").then((r) => r.json()),
      fetch("/api/credit?days=90").then((r) => r.json()),
    ]).then(([dash, credit]) => {
      if (dash.ok) setUser(dash.data.user);
      if (credit.ok) setCreditHistory(credit.data);
      setLoading(false);
    });
  }, []);

  if (loading) return <div className="p-8 text-muted-foreground">Loading...</div>;

  return (
    <div className="p-6 space-y-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold">Settings</h1>

      <Card>
        <CardHeader><CardTitle className="text-sm uppercase tracking-wide">Profile</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Name</span>
            <span>{user?.name}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Credit Score</span>
            <span>{user?.creditScore?.toFixed(1)} ({user?.creditTier})</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Timezone</span>
            <span>Asia/Shanghai</span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm uppercase tracking-wide">Credit History (90 days)</CardTitle></CardHeader>
        <CardContent>
          {creditHistory.length === 0 ? (
            <p className="text-muted-foreground text-sm">No credit events yet</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground text-xs">
                  <th className="text-left">Date</th>
                  <th className="text-left">Source</th>
                  <th className="text-left">Reason</th>
                  <th className="text-right">Before</th>
                  <th className="text-right">Delta</th>
                  <th className="text-right">After</th>
                </tr>
              </thead>
              <tbody>
                {creditHistory.map((e) => (
                  <tr key={e.id} className="border-t border-border">
                    <td className="py-1.5">{new Date(e.occurredAt).toLocaleDateString()}</td>
                    <td>{e.source}</td>
                    <td className="max-w-[200px] truncate">{e.reason}</td>
                    <td className="text-right">{e.scoreBefore.toFixed(1)}</td>
                    <td className={`text-right ${e.delta >= 0 ? "text-green-500" : "text-red-500"}`}>
                      {e.delta > 0 ? "+" : ""}{e.delta.toFixed(1)}
                    </td>
                    <td className="text-right">{e.scoreAfter.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm uppercase tracking-wide">Data</CardTitle></CardHeader>
        <CardContent>
          <Button variant="outline" size="sm" onClick={() => {
            fetch("/api/dashboard").then(r => r.json()).then(j => {
              const blob = new Blob([JSON.stringify(j.data, null, 2)], { type: "application/json" });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url; a.download = "evolution-os-export.json"; a.click();
            });
          }}>
            Export All Data (JSON)
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
