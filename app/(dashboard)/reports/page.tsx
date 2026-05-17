"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export default function ReportsPage() {
  const [reports, setReports] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<any>(null);

  useEffect(() => {
    fetch("/api/reports").then((r) => r.json()).then((j) => {
      if (j.ok) setReports(j.data);
      setLoading(false);
    });
  }, []);

  const daily = reports.filter((r) => r.type === "daily");
  const weekly = reports.filter((r) => r.type === "weekly");

  const renderReport = (r: any) => {
    let content: any = null;
    try { content = JSON.parse(r.content); } catch {}
    const isWeekly = r.type === "weekly";

    return (
      <Card key={r.id} className="cursor-pointer hover:border-accent" onClick={() => setSelected(r)}>
        <CardContent className="pt-4">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium">{r.scopeKey}</span>
            <span className="text-xs text-muted-foreground">{new Date(r.generatedAt).toLocaleString()}</span>
          </div>
          {isWeekly && typeof content === "string" ? (
            <p className="text-sm text-muted-foreground line-clamp-3">{content.substring(0, 200)}...</p>
          ) : content?.verdict ? (
            <p className="text-sm text-muted-foreground">{content.verdict}</p>
          ) : (
            <p className="text-sm text-muted-foreground">No preview</p>
          )}
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="p-6 space-y-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold">Reports</h1>

      <Tabs defaultValue="daily">
        <TabsList>
          <TabsTrigger value="daily">Daily ({daily.length})</TabsTrigger>
          <TabsTrigger value="weekly">Weekly ({weekly.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="daily" className="space-y-3 mt-4">
          {daily.length === 0 ? <p className="text-muted-foreground">No daily reports yet</p> : daily.map(renderReport)}
        </TabsContent>
        <TabsContent value="weekly" className="space-y-3 mt-4">
          {weekly.length === 0 ? <p className="text-muted-foreground">No weekly reports yet</p> : weekly.map(renderReport)}
        </TabsContent>
      </Tabs>

      {selected && (
        <Card>
          <CardHeader>
            <CardTitle>{selected.type} Report: {selected.scopeKey}</CardTitle>
          </CardHeader>
          <CardContent>
            {selected.type === "weekly" ? (
              <div className="prose prose-invert max-w-none text-sm">{selected.content}</div>
            ) : (
              <div className="space-y-3">
                {(() => {
                  let data: any = null;
                  try { data = JSON.parse(selected.content); } catch {}
                  if (!data) return <p>{selected.content}</p>;
                  return (
                    <>
                      {data.verdict && <div className="border-l-4 border-yellow-500 pl-3"><div className="text-xs text-yellow-500 font-semibold">{"\u25B6"} VERDICT</div><p className="text-sm">{data.verdict}</p></div>}
                      {data.risk && <div className="border-l-4 border-red-500 pl-3"><div className="text-xs text-red-500 font-semibold">{"\u26A0"} RISK</div><p className="text-sm">{data.risk}</p></div>}
                      {data.order && <div className="border-l-4 border-blue-500 pl-3"><div className="text-xs text-blue-500 font-semibold">{"\u2192"} ORDER</div><p className="text-sm">{data.order}</p></div>}
                    </>
                  );
                })()}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
