"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

const STEPS = [
  { title: "Welcome to Evolution OS", description: "A personal growth system that rewards only one thing: verified effective hours." },
  { title: "Create Your Skills", description: "What do you want to grow in? Add 2-3 skills to start." },
  { title: "You're Ready", description: "Start by logging your first day on the Dashboard." },
];

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [skills, setSkills] = useState<{ name: string; category: string }[]>([]);
  const [skillName, setSkillName] = useState("");
  const [skillCategory, setSkillCategory] = useState("creation");

  const addSkill = () => {
    if (!skillName.trim()) return;
    setSkills([...skills, { name: skillName, category: skillCategory }]);
    setSkillName("");
  };

  const finish = async () => {
    for (const s of skills) {
      await fetch("/api/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(s),
      });
    }
    toast.success("Setup complete!");
    router.push("/");
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <Card className="max-w-lg w-full">
        <CardHeader>
          <CardTitle>{STEPS[step].title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-muted-foreground">{STEPS[step].description}</p>

          {step === 1 && (
            <div className="space-y-3">
              <div className="flex gap-2">
                <Input
                  placeholder="Skill name (e.g. Novel Writing)"
                  value={skillName}
                  onChange={(e) => setSkillName(e.target.value)}
                />
                <select
                  className="bg-background border border-input rounded-md px-3 py-1.5 text-sm"
                  value={skillCategory}
                  onChange={(e) => setSkillCategory(e.target.value)}
                >
                  {["creation", "engineering", "trading", "body", "business", "learning", "other"].map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
                <Button size="sm" onClick={addSkill}>Add</Button>
              </div>
              {skills.length > 0 && (
                <ul className="space-y-1">
                  {skills.map((s, i) => (
                    <li key={i} className="text-sm flex items-center gap-2">
                      <span className="text-green-500">{"\u2713"}</span>
                      {s.name} <span className="text-muted-foreground">({s.category})</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="flex gap-2 pt-2">
            {step > 0 && <Button variant="outline" onClick={() => setStep(step - 1)}>Back</Button>}
            {step < STEPS.length - 1 ? (
              <Button onClick={() => setStep(step + 1)} disabled={step === 1 && skills.length === 0}>
                {step === 1 && skills.length === 0 ? "Add at least one skill" : "Next"}
              </Button>
            ) : (
              <Button onClick={finish}>Get Started</Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
