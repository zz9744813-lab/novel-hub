"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import { toast } from "sonner";

export default function CommitmentsPage() {
  const [commitments, setCommitments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [skills, setSkills] = useState<any[]>([]);

  // Create form
  const [title, setTitle] = useState("");
  const [type, setType] = useState("hours_based");
  const [targetHours, setTargetHours] = useState(10);
  const [deadline, setDeadline] = useState("");
  const [difficulty, setDifficulty] = useState(3);
  const [completionStandard, setCompletionStandard] = useState("");
  const [linkedSkillId, setLinkedSkillId] = useState("");
  const [realityCheck, setRealityCheck] = useState<any>(null);
  const [checking, setChecking] = useState(false);
  const [creating, setCreating] = useState(false);

  const fetchCommitments = useCallback(async () => {
    try {
      const res = await fetch("/api/commitments");
      const json = await res.json();
      if (json.ok) setCommitments(json.data);
    } catch {} finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchCommitments();
    fetch("/api/skills").then((r) => r.json()).then((j) => { if (j.ok) setSkills(j.data); });
  }, [fetchCommitments]);

  const doRealityCheck = async () => {
    if (!title || !deadline) return;
    setChecking(true);
    try {
      const res = await fetch("/api/ai/reality-check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title, type, startDate: new Date().toISOString().split("T")[0],
          deadline, targetHours, completionStandard, difficulty, linkedSkillId: linkedSkillId || null,
        }),
      });
      const json = await res.json();
      if (json.ok) setRealityCheck(json.data);
    } catch {} finally { setChecking(false); }
  };

  const createCommitment = async () => {
    if (realityCheck?.verdict === "block") {
      toast.error("Reality check blocked. Adjust your target.");
      return;
    }
    setCreating(true);
    try {
      const res = await fetch("/api/commitments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title, type, startDate: new Date().toISOString().split("T")[0],
          deadline, targetHours, completionStandard, difficulty,
          linkedSkillId: linkedSkillId || null,
        }),
      });
      const json = await res.json();
      if (json.ok) {
        toast.success("Commitment created");
        setShowCreate(false);
        setTitle(""); setTargetHours(10); setDeadline(""); setRealityCheck(null);
        fetchCommitments();
      }
    } catch {} finally { setCreating(false); }
  };

  const settle = async (id: string, retroNote: string) => {
    try {
      const res = await fetch(`/api/commitments/${id}/settle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ retroNote }),
      });
      const json = await res.json();
      if (json.ok) {
        toast.success(`Settled! Credit ${json.data.credit.delta > 0 ? "+" : ""}${json.data.credit.delta}`);
        fetchCommitments();
      }
    } catch {}
  };

  const active = commitments.filter((c) => c.status === "active");
  const settled = commitments.filter((c) => ["completed", "late_completed", "partial"].includes(c.status));
  const failed = commitments.filter((c) => ["failed", "abandoned"].includes(c.status));

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Commitments</h1>
        <Button onClick={() => setShowCreate(!showCreate)} size="sm">
          {showCreate ? "Cancel" : "+ New Commitment"}
        </Button>
      </div>

      {showCreate && (
        <Card>
          <CardContent className="pt-6 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Title</label>
                <input className="w-full bg-background border border-input rounded-md px-3 py-1.5 text-sm" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Write 15h this week" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Type</label>
                <select className="w-full bg-background border border-input rounded-md px-3 py-1.5 text-sm" value={type} onChange={(e) => setType(e.target.value)}>
                  <option value="hours_based">Hours Based</option>
                  <option value="outcome_based">Outcome Based</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Target Hours</label>
                <input type="number" className="w-full bg-background border border-input rounded-md px-3 py-1.5 text-sm" value={targetHours} onChange={(e) => setTargetHours(Number(e.target.value))} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Deadline</label>
                <input type="date" className="w-full bg-background border border-input rounded-md px-3 py-1.5 text-sm" value={deadline} onChange={(e) => setDeadline(e.target.value)} />
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Difficulty (1-5)</label>
                <div className="flex gap-1">
                  {[1,2,3,4,5].map((d) => (
                    <button key={d} onClick={() => setDifficulty(d)} className={`w-8 h-8 rounded-md text-sm ${d <= difficulty ? "bg-yellow-500 text-background" : "bg-muted text-muted-foreground"}`}>
                      {d}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-xs text-muted-foreground block mb-1">Linked Skill</label>
                <select className="w-full bg-background border border-input rounded-md px-3 py-1.5 text-sm" value={linkedSkillId} onChange={(e) => setLinkedSkillId(e.target.value)}>
                  <option value="">None</option>
                  {skills.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="text-xs text-muted-foreground block mb-1">Completion Standard</label>
              <Textarea value={completionStandard} onChange={(e) => setCompletionStandard(e.target.value)} placeholder="What does 'done' look like?" rows={2} />
            </div>

            <div className="flex gap-2">
              <Button variant="outline" onClick={doRealityCheck} disabled={checking || !title || !deadline}>
                {checking ? "Checking..." : "Reality Check"}
              </Button>
              <Button onClick={createCommitment} disabled={creating || realityCheck?.verdict === "block"}>
                {creating ? "Creating..." : "Create"}
              </Button>
            </div>

            {realityCheck && (
              <div className={`rounded-md p-3 text-sm ${realityCheck.verdict === "pass" ? "bg-green-900/30 border border-green-800" : realityCheck.verdict === "warn" ? "bg-yellow-900/30 border border-yellow-800" : "bg-red-900/30 border border-red-800"}`}>
                <div className="font-semibold capitalize mb-1">{realityCheck.verdict}</div>
                <p>{realityCheck.reasoning}</p>
                {realityCheck.suggested_target_hours && (
                  <p className="mt-1">Suggested: {realityCheck.suggested_target_hours}h</p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Active */}
      <Card>
        <CardHeader><CardTitle className="text-sm uppercase tracking-wide">Active</CardTitle></CardHeader>
        <CardContent>
          {active.length === 0 ? (
            <p className="text-muted-foreground text-sm">No active commitments</p>
          ) : (
            <div className="space-y-3">
              {active.map((c) => {
                const pct = c.targetHours > 0 ? Math.min(100, (c.actualHours / c.targetHours) * 100) : 0;
                const daysLeft = Math.ceil((new Date(c.deadline).getTime() - Date.now()) / 86400000);
                return (
                  <div key={c.id} className="border border-border rounded-lg p-4 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{c.title}</span>
                      <div className="flex items-center gap-2">
                        <Badge variant="outline">{daysLeft}d left</Badge>
                        <Button size="sm" variant="outline" onClick={() => settle(c.id, "")}>Settle</Button>
                      </div>
                    </div>
                    <Progress value={pct} />
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>{c.actualHours.toFixed(1)} / {c.targetHours}h</span>
                      <span>{pct.toFixed(0)}%</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Settled */}
      {settled.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm uppercase tracking-wide">Settled</CardTitle></CardHeader>
          <CardContent>
            <div className="space-y-2">
              {settled.map((c) => (
                <div key={c.id} className="flex items-center justify-between text-sm">
                  <span>{c.title}</span>
                  <div className="flex items-center gap-2">
                    <Badge variant={c.status === "completed" ? "default" : "secondary"}>{c.status}</Badge>
                    {c.creditDelta != null && (
                      <span className={c.creditDelta >= 0 ? "text-green-500" : "text-red-500"}>
                        {c.creditDelta > 0 ? "+" : ""}{c.creditDelta.toFixed(1)}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
