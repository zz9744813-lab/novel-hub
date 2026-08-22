import { useState, useMemo } from "react";
import { ChevronDown, Search, BookOpen, ShieldCheck, FlaskConical, Ban } from "lucide-react";
import clsx from "clsx";
import type { ResearchScrapeSource } from "../api";

interface SourceSelectorProps {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  sources: ResearchScrapeSource[];
}

function VerificationBadge({ status }: { status: string }) {
  if (status === "verified") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-2xs bg-success/15 text-success border border-success/30">
        <ShieldCheck size={10} />
        已验证
      </span>
    );
  }
  if (status === "disabled") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-2xs bg-danger/15 text-danger border border-danger/30">
        <Ban size={10} />
        已禁用
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-2xs bg-warning/15 text-warning border border-warning/30">
      <FlaskConical size={10} />
      实验性
    </span>
  );
}

export function SourceSelector({
  value,
  onChange,
  disabled = false,
  sources,
}: SourceSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const filteredSources = useMemo(() => {
    if (!searchQuery.trim()) return sources;

    const query = searchQuery.toLowerCase();
    return sources.filter(
      (s) =>
        s.name.toLowerCase().includes(query) ||
        s.code.toLowerCase().includes(query) ||
        (s.config?.description &&
          String(s.config.description).toLowerCase().includes(query)) ||
        (Array.isArray(s.config?.tags) &&
          (s.config.tags as unknown[]).some((tag) =>
            String(tag).toLowerCase().includes(query)
          ))
    );
  }, [sources, searchQuery]);

  const selectedSource = sources.find((s) => s.id === value);

  return (
    <div className="relative">
      <label className="block text-xs font-medium text-text-secondary mb-1.5">
        调研源
      </label>

      <div className="relative">
        <button
          type="button"
          onClick={() => !disabled && setIsOpen(!isOpen)}
          disabled={disabled}
          className={clsx(
            "w-full flex items-center justify-between px-3 py-2.5 rounded-md border bg-bg-panel transition-colors",
            isOpen
              ? "border-brand-accent ring-2 ring-brand-muted/30"
              : "border-border hover:border-border-subtle",
            disabled && "opacity-50 cursor-not-allowed"
          )}
        >
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {selectedSource ? (
              <>
                <BookOpen size={14} className="text-brand-accent shrink-0" />
                <span className="text-body text-text-primary truncate">
                  {selectedSource.name}
                </span>
                <VerificationBadge status={selectedSource.verification_status} />
              </>
            ) : (
              <span className="text-body text-text-disabled">选择调研源...</span>
            )}
          </div>
          <ChevronDown
            size={14}
            className={clsx(
              "text-text-disabled transition-transform",
              isOpen && "rotate-180"
            )}
          />
        </button>

        {isOpen && (
          <>
            <div
              className="fixed inset-0 z-10"
              onClick={() => setIsOpen(false)}
            />

            <div className="absolute z-20 w-full mt-1 bg-bg-panel border border-border rounded-md shadow-lg max-h-64 overflow-hidden animate-modal-in">
              {/* Search input */}
              <div className="px-3 py-2 border-b border-border sticky top-0 bg-bg-panel">
                <div className="relative">
                  <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-disabled" />
                  <input
                    type="text"
                    placeholder="搜索调研源..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full pl-8 pr-3 py-1.5 text-xs bg-bg-surface border border-border rounded-md outline-none focus:border-brand-accent placeholder:text-text-disabled text-text-primary"
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                  />
                </div>
              </div>

              {/* Sources list */}
              <div className="overflow-auto max-h-48 p-1">
                {filteredSources.length === 0 ? (
                  <div className="px-3 py-4 text-center">
                    <p className="text-sm text-text-disabled">未找到匹配的调研源</p>
                  </div>
                ) : (
                  filteredSources.map((source) => (
                    <button
                      key={source.id}
                      type="button"
                      onClick={() => {
                        onChange(source.id);
                        setIsOpen(false);
                        setSearchQuery("");
                      }}
                      className={clsx(
                        "w-full text-left px-3 py-2.5 rounded-md transition-colors",
                        value === source.id
                          ? "bg-brand-muted text-brand-accent"
                          : "hover:bg-bg-hover text-text-secondary hover:text-text-primary"
                      )}
                    >
                      <div className="flex items-start gap-2">
                        <BookOpen
                          size={14}
                          className={clsx(
                            "shrink-0 mt-0.5",
                            value === source.id
                              ? "text-brand-accent"
                              : "text-text-disabled"
                          )}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="text-sm font-medium text-text-primary">
                              {source.name}
                            </span>
                            <VerificationBadge status={source.verification_status} />
                          </div>
                          {source.config?.description != null && (
                            <div className="text-2xs text-text-tertiary mt-0.5 line-clamp-2">
                              {String(source.config.description)}
                            </div>
                          )}
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-2xs font-mono bg-bg-surface text-text-disabled border border-border">
                              {source.encoding.toUpperCase()}
                            </span>
                            {Array.isArray(source.config?.tags) &&
                              (source.config.tags as unknown[])
                                .slice(0, 2)
                                .map((tag) => (
                                  <span
                                    key={String(tag)}
                                    className="inline-flex items-center px-1.5 py-0.5 rounded text-2xs bg-brand-muted/20 text-brand-accent"
                                  >
                                    {String(tag)}
                                  </span>
                                ))}
                          </div>
                        </div>
                      </div>
                    </button>
                  ))
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
