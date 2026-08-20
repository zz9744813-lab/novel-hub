import { useState } from "react";
import clsx from "clsx";

export interface DiffHunk {
  type: "equal" | "insert" | "delete";
  oldText?: string;
  newText?: string;
}

interface DiffViewerProps {
  hunks: DiffHunk[];
  onAcceptAll?: () => void;
  onRejectAll?: () => void;
}

export function DiffViewer({ hunks, onAcceptAll, onRejectAll }: DiffViewerProps) {
  const [acceptAllBusy, setAcceptAllBusy] = useState(false);
  const [rejectAllBusy, setRejectAllBusy] = useState(false);

  const handleAcceptAll = async () => {
    try {
      setAcceptAllBusy(true);
      if (onAcceptAll) await onAcceptAll();
    } finally {
      setTimeout(() => setAcceptAllBusy(false), 500);
    }
  };

  const handleRejectAll = async () => {
    try {
      setRejectAllBusy(true);
      if (onRejectAll) await onRejectAll();
    } finally {
      setTimeout(() => setRejectAllBusy(false), 500);
    }
  };

  // Build final text for display (without diff markers)
  const finalText = hunks
    .filter(h => h.type !== "delete")
    .map(h => h.newText || h.oldText || "")
    .join("");

  return (
    <div className="diff-viewer rounded-card border border-border bg-bg-surface p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2 text-caption text-text-tertiary">
          <span style={{ fontWeight: 510 }}>Patch Preview</span>
          <span className="text-text-disabled">|</span>
          <span>
            {hunks.filter(h => h.type === "insert").length} inserts,{" "}
            {hunks.filter(h => h.type === "delete").length} deletes
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRejectAll}
            disabled={rejectAllBusy}
            className="btn text-xs py-1.5 px-3 rounded-control"
          >
            {rejectAllBusy ? "撤销中…" : "全部拒绝"}
          </button>
          <button
            onClick={handleAcceptAll}
            disabled={acceptAllBusy}
            className="btn-primary text-xs py-1.5 px-3 rounded-control"
          >
            {acceptAllBusy ? "接受中…" : "全部接受"}
          </button>
        </div>
      </div>

      <div className="diff-content font-serif text-body leading-relaxed whitespace-pre-wrap">
        {hunks.map((h, idx) => {
          if (h.type === "equal") {
            return (
              <div key={idx} className="diff-equal">
                {h.oldText || ""}
              </div>
            );
          }
          if (h.type === "insert") {
            return (
              <div key={idx} className="diff-insert" title="新增内容">
                <span className="inline-block min-w-[1ch]" aria-hidden="true" />
                <span>{h.newText}</span>
              </div>
            );
          }
          if (h.type === "delete") {
            return (
              <div key={idx} className="diff-delete line-through text-text-disabled opacity-60" title="删除内容">
                <span className="inline-block min-w-[1ch]" aria-hidden="true" />
                <span>{h.oldText}</span>
              </div>
            );
          }
          return null;
        })}
      </div>

      {/* Hidden final content for downstream consumption */}
      <pre className="hidden">{finalText}</pre>
    </div>
  );
}
