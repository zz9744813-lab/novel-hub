import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ReferenceSample } from "../../api";
import {
  BookOpen,
  FileText,
  FolderOpen,
  Globe,
  Library,
  Loader2,
  Search,
  Trash2,
} from "lucide-react";

type SampleRow = ReferenceSample & {
  book_id: string;
  book_title: string;
};

function formatChars(value: number): string {
  if (value >= 10000) return `${(value / 10000).toFixed(1)} 万字`;
  return `${value.toLocaleString("zh-CN")} 字`;
}

function formatTime(value?: string | null): string {
  if (!value) return "未知时间";
  try {
    return new Date(value).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

export function ReferencesLibraryPage({
  onOpenGenre,
  onOpenResearch,
}: {
  onOpenGenre?: () => void;
  onOpenResearch?: () => void;
}) {
  const [samples, setSamples] = useState<SampleRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [deleting, setDeleting] = useState<SampleRow | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [bookCount, setBookCount] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const lib = await api.library.books();
      const list = lib.books || [];
      setBookCount(list.length);
      const detail = await Promise.all(
        list.slice(0, 40).map(async (b: any) => {
          try {
            const items = await api.genre.listSamples(b.book_id);
            return (items || []).map((sample) => ({
              ...sample,
              book_id: b.book_id,
              book_title: b.title || "未命名",
            }));
          } catch {
            return [] as SampleRow[];
          }
        })
      );
      setSamples(detail.flat().sort((a, b) => (b.uploaded_at || "").localeCompare(a.uploaded_at || "")));
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    if (!q.trim()) return samples;
    const search = q.trim().toLowerCase();
    return samples.filter(
      (sample) =>
        sample.filename.toLowerCase().includes(search) ||
        sample.book_title.toLowerCase().includes(search) ||
        (sample.genre_hint || "").toLowerCase().includes(search)
    );
  }, [samples, q]);

  const handleDelete = async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    setError(null);
    try {
      await api.genre.deleteSample(deleting.book_id, deleting.id);
      setSamples((prev) => prev.filter((sample) => sample.id !== deleting.id));
      setDeleting(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-5">
      <div className="flex flex-wrap items-start gap-3">
        <div>
          <h1 className="text-base text-text-primary" style={{ fontWeight: 510 }}>
            参考资料库
          </h1>
          <p className="text-xs text-text-tertiary mt-0.5">
            管理已上传的参考样本；删除只会移除样本文件，不会删除作品本身
          </p>
        </div>
        <div className="ml-auto flex gap-2">
          {onOpenGenre && (
            <button className="btn text-xs py-1.5 px-3 flex items-center gap-1.5" onClick={onOpenGenre}>
              <BookOpen size={12} /> 文风档案
            </button>
          )}
          {onOpenResearch && (
            <button className="btn text-xs py-1.5 px-3 flex items-center gap-1.5" onClick={onOpenResearch}>
              <Search size={12} /> 调研
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2 min-w-[220px] flex-1 max-w-md rounded-md border border-border bg-bg-panel px-3 py-2 text-text-tertiary">
          <FolderOpen size={13} />
          <input
            className="min-w-0 flex-1 bg-transparent text-xs text-text-primary outline-none placeholder:text-text-disabled"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder="搜索文件名、作品或题材提示"
          />
        </div>
        <span className="text-2xs text-text-disabled font-mono">
          {filtered.length} / {samples.length} 份样本 · 覆盖 {bookCount} 本
        </span>
      </div>

      {error && (
        <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2 flex items-center justify-between gap-3">
          <span>{error}</span>
          <button type="button" className="text-brand-accent" onClick={load}>
            重试
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-text-tertiary text-xs gap-2">
          <Loader2 size={16} className="animate-spin" /> 加载参考资料…
        </div>
      ) : filtered.length === 0 ? (
        <div className="panel-elevated rounded-md px-4 py-10 text-center space-y-2">
          <Library size={22} className="mx-auto text-text-disabled" />
          <p className="text-xs text-text-tertiary">
            {samples.length === 0 ? "还没有参考样本" : "没有匹配的样本"}
          </p>
          <p className="text-2xs text-text-disabled">
            在系统「文风档案」里上传样本后，会显示在这里，并可直接删除
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {filtered.map((sample) => (
            <div key={sample.id} className="panel-elevated rounded-md p-3.5 space-y-3">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 rounded-md border border-border bg-bg-surface p-2 text-brand-accent">
                  <FileText size={14} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="text-xs text-text-primary truncate" style={{ fontWeight: 510 }}>
                    {sample.filename}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-text-tertiary">
                    <span className="flex items-center gap-1 truncate">
                      <BookOpen size={10} /> {sample.book_title}
                    </span>
                    <span className="font-mono">{formatChars(sample.character_count || 0)}</span>
                    <span className="font-mono">{formatTime(sample.uploaded_at)}</span>
                  </div>
                </div>
                <button
                  type="button"
                  className="btn-danger text-2xs py-1.5 px-2.5 shrink-0"
                  title="删除这份参考样本"
                  onClick={() => setDeleting(sample)}
                >
                  <Trash2 size={12} /> 删除
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-2xs">
                <span className="rounded-full border border-border px-2 py-0.5 text-text-secondary">
                  {sample.status || "ready"}
                </span>
                {sample.genre_hint ? (
                  <span className="rounded-full border border-border px-2 py-0.5 text-text-tertiary flex items-center gap-1">
                    <Globe size={10} /> {sample.genre_hint}
                  </span>
                ) : (
                  <span className="text-text-disabled">无题材提示</span>
                )}
                <span className="ml-auto font-mono text-text-disabled">{sample.id.slice(0, 8)}…</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {deleting && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-bg-canvas/70 p-4"
          onClick={() => !deleteBusy && setDeleting(null)}
        >
          <div
            className="w-full max-w-sm rounded-lg border border-border bg-bg-panel p-5 shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 className="text-sm text-text-primary font-medium">删除参考样本</h2>
            <p className="mt-2 text-xs text-text-secondary leading-5">
              确定删除《{deleting.book_title}》下的「{deleting.filename}」吗？压缩源文件会一起移除，已生成的文风档案不会自动删除。
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn text-xs px-3 py-1.5" disabled={deleteBusy} onClick={() => setDeleting(null)}>
                取消
              </button>
              <button
                className="btn-danger text-xs px-3 py-1.5 flex items-center gap-1.5"
                disabled={deleteBusy}
                onClick={handleDelete}
              >
                {deleteBusy && <Loader2 size={12} className="animate-spin" />}
                {deleteBusy ? "删除中…" : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
