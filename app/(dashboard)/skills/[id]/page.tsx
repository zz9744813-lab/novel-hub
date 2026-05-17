"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { MasteryPath } from "@/components/viz/mastery-path";
import { toast } from "sonner";

export default function SkillDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const [id, setId] = useState<string>("");
  const [skill, setSkill] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [assessing, setAssessing] = useState(false);

  useEffect(() => { params.then((p) => setId(p.id)); }, [params]);

  const fetchSkill = useCallback(async () => {
    if (!id) return;
    try {
      const res = await fetch(`/api/skills/${id}`);
      const json = await res.json();
      if (json.ok) setSkill(json.data);
    } catch {} finally { setLoading(false); }
  }, [id]);

  useEffect(() => { fetchSkill(); }, [fetchSkill]);

  const assess = async () => {
    setAssessing(true);
    try {
      const res = await fetch("/api/ai/skill-assessment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skillId: id }),
      });
      const json = await res.json();
      if (json.ok) {
        toast.success("Assessment complete");
        fetchSkill();
      } else {
        toast.error(json.error?.message ?? "Assessment failed");
      }
    } catch { toast.error("Network error"); } finally { setAssessing(false); }
  };

  if (loading) return <div className="p-8 text-muted-foreground">Loading...</div>;
  if (!skill) return <div className="p-8 text-muted-foreground">Skill not found</div>;

  const levels = skill.levels ?? [];
  const lvName = levels.find((l: any) => l.level === skill.currentLevel)?.levelName ?? "";
  const nextLv = levels.find((l: any) => l.level === skill.currentLevel + 1);
  const hoursToNext = nextLv ? Math.max(0, nextLv.minEffectiveHours - skill.effectiveHoursTotal) : 0;

  const defaultLevels = [
    { level: 0, levelName: "未入门" }, { level: 1, levelName: "入门" }, { level: 2, levelName: "初级" },
    { level: 3, levelName: "合格" }, { level: 4, levelName: "熟练" }, { level: 5, levelName: "专业" },
    { level: 6, levelName: "专家" }, { level: 7, levelName: "大师候选" }, { level: 8, levelName: "大师" },
  ];

  let strengths: string[] = [];
  let weaknesses: string[] = [];
  try { strengths = JSON.parse(skill.strengths ?? "[]"); } catch {}
  try { weaknesses = JSON.parse(skill.weaknesses ?? "[]"); } catch {}

  return (
    <div className="p-6 space-y-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{skill.name}</h1>
          <p className="text-muted-foreground">{skill.category} &middot; L{skill.currentLevel} {lvName}</p>
        </div>
        <Button onClick={assess} disabled={assessing} size="sm">
          {assessing ? "Assessing..." : "AI Assessment"}
        </Button>
      </div>

      <MasteryPath levels={defaultLevels} current={skill.currentLevel} />

      <div className="grid grid-cols-4 gap-4">
        <Card><CardContent className="pt-6 text-center"><div className="text-2xl font-bold">{skill.rawHoursTotal.toFixed(0)}</div><div className="text-xs text-muted-foreground">Raw Hours</div></CardContent></Card>
        <Card><CardContent className="pt-6 text-center"><div className="text-2xl font-bold">{skill.effectiveHoursTotal.toFixed(0)}</div><div className="text-xs text-muted-foreground">Effective Hours</div></CardContent></Card>
        <Card><CardContent className="pt-6 text-center"><div className="text-2xl font-bold">{skill.deliberateHoursTotal.toFixed(0)}</div><div className="text-xs text-muted-foreground">Deliberate</div></CardContent></Card>
        <Card><CardContent className="pt-6 text-center"><div className="text-2xl font-bold">{hoursToNext.toFixed(0)}h</div><div className="text-xs text-muted-foreground">To L{skill.currentLevel + 1}</div></CardContent></Card>
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="entries">Recent Entries</TabsTrigger>
          <TabsTrigger value="ladder">Ladder</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <Card>
            <CardContent className="pt-6 space-y-4">
              {strengths.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold mb-2">Strengths</h3>
                  <ul className="list-disc list-inside text-sm space-y-1">
                    {strengths.map((s: string, i: number) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}
              {weaknesses.length > 0 && (
                <div>
                  <h3 className="text-sm font-semibold mb-2">Weaknesses</h3>
                  <ul className="list-disc list-inside text-sm space-y-1">
                    {weaknesses.map((w: string, i: number) => <li key={i}>{w}</li>)}
                  </ul>
                </div>
              )}
              {skill.aiComment && <p className="text-sm text-muted-foreground italic">{skill.aiComment}</p>}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="entries">
          <Card>
            <CardContent className="pt-6">
              {skill.logEntries?.length === 0 ? (
                <p className="text-muted-foreground text-sm">No entries yet</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-muted-foreground text-xs">
                      <th className="text-left">Date</th>
                      <th className="text-left">Activity</th>
                      <th className="text-right">Raw</th>
                      <th className="text-right">Effective</th>
                    </tr>
                  </thead>
                  <tbody>
                    {skill.logEntries?.map((e: any) => (
                      <tr key={e.id} className="border-t border-border">
                        <td className="py-1.5">{new Date(e.timeLog.date).toLocaleDateString()}</td>
                        <td>{e.activity}</td>
                        <td className="text-right">{e.rawHours.toFixed(1)}h</td>
                        <td className="text-right">{e.effectiveHours.toFixed(1)}h</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="ladder">
          <Card>
            <CardContent className="pt-6 space-y-3">
              {levels.map((lv: any) => (
                <div key={lv.level} className="flex items-start gap-3">
                  <Badge variant={lv.level <= skill.currentLevel ? "default" : "outline"}>L{lv.level}</Badge>
                  <div>
                    <div className="font-medium">{lv.levelName}</div>
                    <div className="text-xs text-muted-foreground">{lv.minEffectiveHours}-{lv.maxEffectiveHours}h &middot; {lv.assessmentCriteria}</div>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
