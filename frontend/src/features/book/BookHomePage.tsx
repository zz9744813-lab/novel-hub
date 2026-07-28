import { useEffect, useState } from "react";
import { api } from "../../api";
import { Play, Loader2, BookOpen, Users, Map, GitBranch } from "lucide-react";

export function BookHomePage({
  bookId,
  onContinueWrite,
  onOpenChapters,
}: {
  bookId: string;
  onContinueWrite: () => void;
  onOpenChapters: () => void;
}) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await api.library.bookHome(bookId);
        if (!cancelled) setData(d);
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bookId]);

  const book = data?.book;
  const style = book?.cover_style;

  const handleContinue = async () => {
    setBusy(true);
    try {
      await api.chapters.runNext(bookId);
      onContinueWrite();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  if (err && !data) {
    return <div className="p-6 text-xs text-red-400">{err}</div>;
  }
  if (!data) {
    return (
      <div className="p-10 flex items-center gap-2 text-xs text-text-tertiary">
        <Loader2 size={14} className="animate-spin" /> 加载作品首页…
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-5">
      <div className="flex flex-col md:flex-row gap-5">
        <div
          className="w-full md:w-40 h-56 rounded-lg shrink-0 border border-border shadow-md"
          style={{ background: style?.background || "#1a1a2e" }}
        />
        <div className="flex-1 min-w-0">
          <h1 className="text-lg text-text-primary" style={{ fontWeight: 510 }}>
            {book?.title}
          </h1>
          {book?.logline && <p className="text-xs text-text-tertiary mt-2">{book.logline}</p>}
          <div className="flex flex-wrap gap-2 mt-3">
            {(book?.tags || []).map((t: string) => (
              <span key={t} className="text-2xs border border-border rounded px-2 py-0.5 text-text-secondary">
                {t}
              </span>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <Stat label="已定稿" value={`${book?.finalized_chapters ?? 0} 章`} />
            <Stat label="计划" value={`${book?.planned_chapters ?? "—"} 章`} />
            <Stat label="字数" value={`${(book?.finalized_words || 0).toLocaleString()}`} />
            <Stat label="状态" value={book?.lifecycle_status || "—"} />
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            <button
              onClick={handleContinue}
              disabled={busy}
              className="btn-primary text-xs py-2 px-4 flex items-center gap-1.5"
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
              继续写下一章
            </button>
            <button onClick={onOpenChapters} className="btn text-xs py-2 px-3">
              打开章节
            </button>
          </div>
          {err && <p className="text-xs text-red-400 mt-2">{err}</p>}
          {book?.active_task && (
            <p className="text-xs text-brand-accent mt-3">{book.active_task.label}</p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Mini icon={Users} label="人物" value={data.counts?.characters ?? 0} />
        <Mini icon={Map} label="世界规则" value={data.counts?.world_rules ?? 0} />
        <Mini icon={GitBranch} label="大纲节点" value={data.counts?.outline_nodes ?? 0} />
        <Mini icon={BookOpen} label="下一动作" value={data.next_action || "—"} small />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel-elevated rounded-md px-3 py-2">
      <div className="text-2xs text-text-disabled">{label}</div>
      <div className="text-sm text-text-primary mt-0.5 font-mono">{value}</div>
    </div>
  );
}

function Mini({
  icon: Icon,
  label,
  value,
  small,
}: {
  icon: any;
  label: string;
  value: any;
  small?: boolean;
}) {
  return (
    <div className="panel rounded-md p-3 flex items-start gap-2">
      <Icon size={14} className="text-brand-accent mt-0.5" />
      <div>
        <div className="text-2xs text-text-disabled">{label}</div>
        <div className={small ? "text-xs text-text-secondary mt-0.5" : "text-sm text-text-primary font-mono mt-0.5"}>
          {value}
        </div>
      </div>
    </div>
  );
}
