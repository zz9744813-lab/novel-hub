import { useEffect, useState, useCallback, useRef } from "react";

interface CommandItem {
  id: string;
  label: string;
  action: () => void;
  category: "书籍" | "写作" | "系统";
}

interface CommandPaletteProps {
  items: CommandItem[];
  onClose?: () => void;
}

export function CommandPalette({ items, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const [filteredItems, setFilteredItems] = useState<CommandItem[]>(items);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose?.();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => Math.min(prev + 1, filteredItems.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => Math.max(prev - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (filteredItems[selectedIndex]) {
          filteredItems[selectedIndex].action();
          onClose?.();
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose, selectedIndex, filteredItems]);

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.focus();
    }
  }, []);

  useEffect(() => {
    const q = query.toLowerCase().trim();
    if (!q) {
      setFilteredItems(items);
      setSelectedIndex(0);
      return;
    }
    const filtered = items.filter(item => item.label.toLowerCase().includes(q) || item.id.toLowerCase().includes(q));
    setFilteredItems(filtered);
    setSelectedIndex(0);
  }, [query, items]);

  if (!items.length) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[9999] flex items-start justify-center pt-24">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-xl bg-bg-panel border border-border rounded-card shadow-2xl overflow-hidden animate-modal-in">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <kbd className="text-2xs text-text-disabled font-mono bg-bg-surface px-1.5 py-0.5 rounded border border-border-subtle hidden sm:inline">
            ⌘K
          </kbd>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索命令..."
            className="flex-1 bg-transparent border-0 outline-none text-body text-text-primary placeholder:text-text-disabled"
          />
        </div>
        <div className="max-h-[60vh] overflow-auto py-2">
          {filteredItems.map((item, idx) => (
            <button
              key={item.id}
              onClick={() => {
                item.action();
                onClose?.();
              }}
              className={`w-full text-left px-4 py-2.5 hover:bg-bg-hover transition-colors ${
                idx === selectedIndex ? "bg-brand-muted" : ""
              }`}
            >
              <div className="flex items-center justify-between">
                <span style={{ fontWeight: 510 }}>{item.label}</span>
                <span className="text-caption text-text-disabled">{item.category}</span>
              </div>
            </button>
          ))}
          {filteredItems.length === 0 && (
            <div className="px-4 py-8 text-center text-body text-text-tertiary">
              未找到匹配的命令
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Hook to trigger command palette globally
let commandPaletteCallback: ((cb: () => void) => void) | null = null;

export function useCommandPalette() {
  const show = useCallback((callback: typeof commandPaletteCallback) => {
    commandPaletteCallback = callback;
  }, []);
  
  return { show, hide: () => (commandPaletteCallback = null) };
}
