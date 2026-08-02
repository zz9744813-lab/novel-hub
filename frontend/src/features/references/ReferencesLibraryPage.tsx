import { useEffect, useState } from "react";
import { api } from "../../api";
import { FileText, Loader2, Library, BookOpen, Globe, Search } from "lucide-react";

/**
 * Minimal usable References library shell:
 * - list books' genre profiles / research sessions counts
 * - entry points into system Genre / Research tabs (via tip)
 */
export function ReferencesLibraryPage({
  onOpenGenre,
  onOpenResearch,
}: {
  onOpenGenre?: () => void;
  onOpenResearch?: () => void;
}) {
  const [books, setBooks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rows, setRows] = useState<
    { book_id: string; title: string; genre_count: number; research_count: number }[]
  >([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const lib = await api.library.books();
        const list = lib.books || [];
        if (cancelled) return;
        setBooks(list);
        const detail = await Promise.all(
          list.slice(0, 24).map(async (b: any) => {
            let genre_count = 0;
            let research_count = 0;
            try {
              const g: any = await api.genre.list(b.book_id);
              genre_count = Array.isArray(g) ? g.length : Array.isArray(g?.profiles) ? g.profiles.length : 0;
            } catch {
              /* ignore */
            }
            try {
              const researchResponse: any = await api.research.list(b.book_id);
              research_count = Array.isArray(researchResponse) ? researchResponse.length : Array.isArray(researchResponse?.sessions) ? researchResponse.sessions.length : 0;
            } catch {
              /* ignore */
            }
            return {
              book_id: b.book_id,
              title: b.title || "未命名",
              genre_count,
              research_count,
            };
          })
        );
        if (!cancelled) setRows(detail);
      } catch (e: any) {
        if (!cancelled) setError(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="h-full overflow-auto p-4 md:p-6 space-y-5">
      <div className="flex flex-wrap items-start gap-3">
        <div>
          <h1 className="text-base text-text-primary" style={{ fontWeight: 510 }}>
            参考资料库
          </h1>
          <p className="text-xs text-text-tertiary mt-0.5">
            按作品汇总文风档案（Genre）与调研会话；上传样本请进系统「文风 / 调研」
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

      {error && (
        <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-text-tertiary text-xs gap-2">
          <Loader2 size={16} className="animate-spin" /> 加载参考资料…
        </div>
      ) : rows.length === 0 ? (
        <div className="panel-elevated rounded-md px-4 py-10 text-center space-y-2">
          <Library size={22} className="mx-auto text-text-disabled" />
          <p className="text-xs text-text-tertiary">书架为空时这里也没有资料条目</p>
          <p className="text-2xs text-text-disabled">先在「我的书架」导入/创建作品，再上传参考样本</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {rows.map((r) => (
            <div key={r.book_id} className="panel-elevated rounded-md p-3 space-y-2">
              <div className="flex items-center gap-2">
                <FileText size={13} className="text-brand-accent shrink-0" />
                <span className="text-xs text-text-primary truncate" style={{ fontWeight: 510 }}>
                  {r.title}
                </span>
              </div>
              <div className="flex gap-3 text-2xs text-text-tertiary font-mono">
                <span className="flex items-center gap-1">
                  <BookOpen size={10} /> Genre {r.genre_count}
                </span>
                <span className="flex items-center gap-1">
                  <Globe size={10} /> 调研 {r.research_count}
                </span>
              </div>
              <p className="text-2xs text-text-disabled truncate">{r.book_id.slice(0, 8)}…</p>
            </div>
          ))}
        </div>
      )}

      <p className="text-2xs text-text-disabled text-center">
        共 {books.length} 本 · 展示前 {rows.length} · 数据来自 GenreProfile + ResearchSession
      </p>
    </div>
  );
}
