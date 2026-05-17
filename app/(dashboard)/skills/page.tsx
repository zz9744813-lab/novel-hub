"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { SkillProgress } from "@/components/viz/skill-progress";

export default function SkillsPage() {
  const [skills, setSkills] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCategory, setNewCategory] = useState("creation");

  const fetchSkills = useCallback(async () => {
    try {
      const res = await fetch("/api/skills");
      const json = await res.json();
      if (json.ok) setSkills(json.data);
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchSkills(); }, [fetchSkills]);

  const createSkill = async () => {
    if (!newName.trim()) return;
    try {
      const res = await fetch("/api/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName, category: newCategory }),
      });
      const json = await res.json();
      if (json.ok) {
        setNewName("");
        setShowCreate(false);
        fetchSkills();
      }
    } catch {}
  };

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Skills</h1>
        <Button onClick={() => setShowCreate(!showCreate)} size="sm">
          {showCreate ? "Cancel" : "+ New Skill"}
        </Button>
      </div>

      {showCreate && (
        <Card>
          <CardContent className="pt-6 flex gap-4 items-end">
            <div className="flex-1">
              <label className="text-xs text-muted-foreground mb-1 block">Skill Name</label>
              <input
                className="w-full bg-background border border-input rounded-md px-3 py-1.5 text-sm"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Novel Writing"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Category</label>
              <select
                className="bg-background border border-input rounded-md px-3 py-1.5 text-sm"
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
              >
                {["creation", "engineering", "trading", "body", "business", "learning", "other"].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
            <Button onClick={createSkill} size="sm">Create</Button>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : skills.length === 0 ? (
        <Card>
          <CardContent className="pt-6 text-center text-muted-foreground">
            No skills yet. Create one to start tracking your growth.
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-4">
          {skills.map((s) => {
            const lvName = s.levels?.find((l: any) => l.level === s.currentLevel)?.levelName ?? "";
            const nextLv = s.levels?.find((l: any) => l.level === s.currentLevel + 1);
            const hoursToNext = nextLv ? Math.max(0, nextLv.minEffectiveHours - s.effectiveHoursTotal) : 0;
            return (
              <Link key={s.id} href={`/skills/${s.id}`}>
                <SkillProgress
                  name={s.name}
                  level={s.currentLevel}
                  levelName={lvName}
                  effectiveHours={s.effectiveHoursTotal}
                  hoursToNext={hoursToNext}
                  description={s.description ?? undefined}
                />
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
