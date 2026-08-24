#!/usr/bin/env node
/**
 * v9.5 theme audit (spec §99–§100).
 *
 * Scans src/** *.tsx and *.css for theme hardcoding that violates the
 * semantic token system. Exit 0 = clean, exit 1 = violations found.
 */
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..", "src");

const RULES = [
  { name: "bg-white/bg-black/text-white/text-black class", re: /\b(?:bg|text|border|hover:bg|focus:bg)-(?:white|black)\b/g },
  { name: "gray palette class (gray/slate/zinc/stone/neutral)", re: /\b(?:text|bg|border)-(?:gray|slate|zinc|stone|neutral)-\d+/g },
  { name: "inline style hex color", re: /style=\{\{[^}]*#[0-9a-fA-F]{3,8}\b/g },
];

const ALLOW_FILES = [
  // cover-art overlays need a dark scrim over images regardless of theme
  "BookList.tsx",
  "ChapterList.tsx",
];

function collect(dir, out) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) collect(p, out);
    else if (p.endsWith(".tsx") || p.endsWith(".css")) out.push(p);
  }
}

const files = [];
collect(ROOT, files);
const violations = [];

for (const file of files) {
  const rel = path.relative(ROOT, file);
  if (ALLOW_FILES.some((f) => rel.endsWith(f))) continue;
  const content = fs.readFileSync(file, "utf-8");
  for (const rule of RULES) {
    let m;
    rule.re.lastIndex = 0;
    while ((m = rule.re.exec(content)) !== null) {
      violations.push(`${rel}: ${rule.name} → ${JSON.stringify(m[0])}`);
    }
  }
}

if (violations.length) {
  console.error(`theme-audit: ${violations.length} violation(s)\n`);
  for (const v of violations.slice(0, 100)) console.error("  " + v);
  process.exit(1);
}
console.log("theme-audit: clean ✦ all colors through semantic tokens");
process.exit(0);
