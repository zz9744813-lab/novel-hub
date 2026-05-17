"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MasteryPath } from "@/components/viz/mastery-path";

const DEFAULT_LEVELS = [
  { level: 0, levelName: "未入门", criteria: "只知道概念，不能独立完成" },
  { level: 1, levelName: "入门", criteria: "能照着教程做，依赖模板" },
  { level: 2, levelName: "初级", criteria: "能完成简单任务，错误较多" },
  { level: 3, levelName: "合格", criteria: "能独立完成基础项目" },
  { level: 4, levelName: "熟练", criteria: "稳定交付，有自己的方法" },
  { level: 5, levelName: "专业", criteria: "能解决复杂问题，可商用" },
  { level: 6, levelName: "专家", criteria: "有体系化能力，能指导他人" },
  { level: 7, levelName: "大师候选", criteria: "有代表作、方法论、风格" },
  { level: 8, levelName: "大师", criteria: "稀缺能力 + 影响力 + 代表成果" },
];

export default function LadderPage() {
  const [skills, setSkills] = useState<any[]>([]);

  useEffect(() => {
    fetch("/api/skills").then((r) => r.json()).then((j) => {
      if (j.ok) setSkills(j.data);
    });
  }, []);

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold">Ladder</h1>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wide">Global L0-L8 Reference</CardTitle>
        </CardHeader>
        <CardContent>
          <MasteryPath levels={DEFAULT_LEVELS} current={-1} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wide">Skill Matrix</CardTitle>
        </CardHeader>
        <CardContent>
          {skills.length === 0 ? (
            <p className="text-muted-foreground text-sm">No skills yet</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-muted-foreground text-xs">
                    <th className="text-left py-2">Skill</th>
                    {DEFAULT_LEVELS.map((lv) => (
                      <th key={lv.level} className="text-center px-2">L{lv.level}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {skills.map((s) => (
                    <tr key={s.id} className="border-t border-border">
                      <td className="py-2 font-medium">{s.name}</td>
                      {DEFAULT_LEVELS.map((lv) => (
                        <td key={lv.level} className="text-center px-2">
                          {lv.level <= s.currentLevel ? (
                            <span className="text-green-500">{"\u2713"}</span>
                          ) : (
                            <span className="text-muted-foreground">{"\u2013"}</span>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wide">Level Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {DEFAULT_LEVELS.map((lv) => (
            <div key={lv.level} className="flex items-start gap-3">
              <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-sm font-bold">
                {lv.level}
              </div>
              <div>
                <div className="font-medium">{lv.levelName}</div>
                <div className="text-xs text-muted-foreground">{lv.criteria}</div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
