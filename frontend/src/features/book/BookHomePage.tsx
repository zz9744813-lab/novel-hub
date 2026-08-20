import { useEffect, useState } from "react";
import { api, fetchAuthenticatedAsset } from "../../api";
import { Play, Loader2, BookOpen, Users, Map, GitBranch, MapPin, ScrollText, Sparkles, Check } from "lucide-react";

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
  const [ctx, setCtx] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [coverSrc, setCoverSrc] = useState<string | null>(null);
  const [planning, setPlanning] = useState<any>(null);
  const [planningBusy, setPlanningBusy] = useState(false);
  const [planningErr, setPlanningErr] = useState<string | null>(null);
  const [premise, setPremise] = useState("");
  const [planningGenre, setPlanningGenre] = useState("");
  const [planningTone, setPlanningTone] = useState("");
  const [planningThemes, setPlanningThemes] = useState("");
  const [targetChapterCount, setTargetChapterCount] = useState(12);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setCoverSrc(null);
    const coverUrl = data?.book?.cover_url;
    if (!coverUrl) return () => undefined;
    fetchAuthenticatedAsset(coverUrl).then((url) => {
      objectUrl = url;
      if (active) setCoverSrc(url);
    }).catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [data?.book?.cover_url]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await api.library.bookHome(bookId);
        if (!cancelled) setData(d);
        try {
          const c = await (api.library as any).contextPreview?.(bookId);
          if (!cancelled && c) setCtx(c);
        } catch {
          /* optional */
        }
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bookId]);

  const handleGenerateCover = async () => {
    setCoverBusy(true);
    setErr(null);
    try {
      await api.books.generateCover(bookId);
      setData(await api.library.bookHome(bookId));
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setCoverBusy(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    api.planning.get(bookId).then((p) => {
      if (!cancelled) setPlanning(p);
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [bookId]);

  const handleGeneratePlanning = async () => {
    if (!premise.trim()) {
      setPlanningErr("请先输入故事 premise");
      return;
    }
    setPlanningBusy(true);
    setPlanningErr(null);
    try {
      const result = await api.planning.generate(bookId, {
        premise: premise.trim(),
        genre: planningGenre.trim() || undefined,
        tone: planningTone.trim() || undefined,
        themes: planningThemes.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
        target_chapter_count: targetChapterCount,
      });
      setPlanning(result);
    } catch (e: any) {
      setPlanningErr(e?.message || String(e));
    } finally {
      setPlanningBusy(false);
    }
  };

  const handleConfirmPlanning = async () => {
    if (!planning?.outline_version_id) return;
    setPlanningBusy(true);
    setPlanningErr(null);
    try {
      await api.planning.confirm(bookId, planning.outline_version_id);
      setPlanning(await api.planning.get(bookId));
      setData(await api.library.bookHome(bookId));
    } catch (e: any) {
      setPlanningErr(e?.message || String(e));
    } finally {
      setPlanningBusy(false);
    }
  };

  const book = data?.book;
  const style = book?.cover_style;
  const counts = data?.counts || {};
  const entities = data?.entities || {};
  const profile = data?.profile;
  const isBlankBook = !book?.source_import_session_id && !book?.planned_chapters && book?.lifecycle_status === "draft";

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
        >
          {coverSrc ? (
            <img src={coverSrc} alt={`${book?.title || ""} 封面`} className="h-full w-full object-cover" />
          ) : null}
        </div>
        <div className="flex-1 min-w-0">
          <h1 className="text-lg text-text-primary" style={{ fontWeight: 510 }}>
            {book?.title}
          </h1>
          {(book?.logline || profile?.logline) && (
            <p className="text-xs text-text-tertiary mt-2">{book?.logline || profile?.logline}</p>
          )}
          <div className="flex flex-wrap gap-2 mt-3">
            {(book?.tags || []).map((t: string) => (
              <span key={t} className="text-2xs border border-border rounded px-2 py-0.5 text-text-secondary">
                {t}
              </span>
            ))}
            {book?.genre && !(book?.tags || []).includes(book.genre) && (
              <span className="text-2xs border border-border rounded px-2 py-0.5 text-text-secondary">
                {book.genre}
              </span>
            )}
          </div>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <Stat label="已定稿" value={`${book?.finalized_chapters ?? 0} 章`} accent="success" />
            <Stat label="计划" value={`${book?.planned_chapters ?? "—"} 章`} />
            <Stat label="字数" value={`${(book?.finalized_words || 0).toLocaleString()}`} />
            <Stat label="状态" value={book?.lifecycle_status || "—"} />
          </div>
          <div className="mt-5 pt-4 border-t border-border/50 flex flex-wrap gap-2">
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
            <button
              onClick={handleGenerateCover}
              disabled={coverBusy}
              className="btn text-xs py-2 px-3 flex items-center gap-1.5"
            >
              {coverBusy ? <Loader2 size={13} className="animate-spin" /> : null}
              {book?.cover_url ? "重新生成封面" : "生成封面"}
            </button>
          </div>
          {err && <p className="text-xs text-red-400 mt-2">{err}</p>}
          {book?.active_task && (
            <p className="text-xs text-brand-accent mt-3">{book.active_task.label}</p>
          )}
        </div>
      </div>

      {isBlankBook && (planning?.status === "approved" ? (
        <div className="panel p-4 flex items-center gap-2 text-xs text-emerald-300">
          <Check size={14} />
          AI 规划已确认 · {planning?.draft?.chapters?.length || planning?.version || 0} 章已写入正式章纲
        </div>
      ) : (
        <section className="panel p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Sparkles size={15} className="text-brand-accent" />
            <div>
              <h2 className="text-sm text-text-primary">空白作品 AI 规划</h2>
              <p className="text-2xs text-text-tertiary mt-0.5">
                先生成可审阅草案，确认后才会创建正式章纲和作品设定。
              </p>
            </div>
          </div>
          <textarea
            value={premise}
            onChange={(e) => setPremise(e.target.value)}
            placeholder="输入故事 premise：主角、核心冲突、世界背景或你已有的灵感……"
            className="w-full min-h-20 rounded-md border border-border bg-bg-base px-3 py-2 text-xs text-text-primary placeholder:text-text-disabled outline-none focus:border-brand-accent"
          />
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
            <input
              value={planningGenre}
              onChange={(e) => setPlanningGenre(e.target.value)}
              placeholder="题材（可选）"
              className="rounded-md border border-border bg-bg-base px-3 py-2 text-xs text-text-primary placeholder:text-text-disabled outline-none focus:border-brand-accent"
            />
            <input
              value={planningTone}
              onChange={(e) => setPlanningTone(e.target.value)}
              placeholder="基调（可选）"
              className="rounded-md border border-border bg-bg-base px-3 py-2 text-xs text-text-primary placeholder:text-text-disabled outline-none focus:border-brand-accent"
            />
            <input
              value={planningThemes}
              onChange={(e) => setPlanningThemes(e.target.value)}
              placeholder="主题，逗号分隔"
              className="rounded-md border border-border bg-bg-base px-3 py-2 text-xs text-text-primary placeholder:text-text-disabled outline-none focus:border-brand-accent"
            />
            <input
              type="number"
              min={1}
              max={500}
              value={targetChapterCount}
              onChange={(e) => setTargetChapterCount(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
              aria-label="目标章节数"
              className="rounded-md border border-border bg-bg-base px-3 py-2 text-xs text-text-primary outline-none focus:border-brand-accent"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={handleGeneratePlanning}
              disabled={planningBusy}
              className="btn-primary text-xs py-2 px-3 flex items-center gap-1.5"
            >
              {planningBusy ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
              生成规划草案
            </button>
            {planning?.status === "draft" && planning?.outline_version_id && (
              <button
                onClick={handleConfirmPlanning}
                disabled={planningBusy}
                className="btn text-xs py-2 px-3 flex items-center gap-1.5"
              >
                <Check size={13} /> 确认并写入章纲
              </button>
            )}
            {planning?.status && planning.status !== "not_started" && (
              <span className="text-2xs text-text-disabled">状态：{planning.status}</span>
            )}
          </div>
          {planningErr && <p className="text-xs text-red-400">{planningErr}</p>}
          {planning?.draft && (
            <div className="border-t border-border pt-3 space-y-2">
              <div className="text-xs text-text-primary">
                {planning.draft.title} · {planning.draft.logline}
              </div>
              <p className="text-2xs text-text-secondary">{planning.draft.synopsis}</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-72 overflow-auto">
                {(planning.draft.chapters || []).map((chapter: any) => (
                  <div key={chapter.chapter_no} className="rounded-md border border-border bg-bg-base p-2">
                    <div className="text-2xs text-text-primary">
                      第{chapter.chapter_no}章 · {chapter.title || "未命名"}
                    </div>
                    <div className="text-2xs text-text-tertiary mt-1">{chapter.goal}</div>
                    {!!chapter.required_beats?.length && (
                      <div className="text-2xs text-text-disabled mt-1">
                        节拍：{chapter.required_beats.join("、")}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      ))}

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
        <Mini icon={Users} label="人物" value={counts.characters ?? 0} />
        <Mini icon={MapPin} label="地点" value={counts.locations ?? 0} />
        <Mini icon={Map} label="世界规则" value={counts.world_rules ?? 0} />
        <Mini icon={GitBranch} label="大纲" value={counts.outline_nodes ?? 0} />
        <Mini icon={ScrollText} label="剧情线" value={counts.plot_threads ?? 0} />
        <Mini icon={BookOpen} label="写作规则" value={counts.writing_constraints ?? 0} />
        <Mini icon={GitBranch} label="卷" value={counts.volumes ?? 0} />
        <Mini icon={Users} label="关系" value={counts.relationships ?? 0} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <EntityList title="人物" items={(entities.characters || []).map((c: any) => c.name + (c.role ? ` · ${c.role}` : ""))} />
        <EntityList title="地点" items={(entities.locations || []).map((l: any) => l.name)} />
        <EntityList
          title="章纲预览"
          items={(entities.outline_preview || []).map(
            (n: any) => `第${n.chapter_no}章 ${n.title || n.goal || ""}`
          )}
        />
        <EntityList
          title="剧情线"
          items={(entities.plot_threads || []).map((t: any) => `${t.name}${t.status ? ` (${t.status})` : ""}`)}
        />
        <EntityList
          title="世界规则"
          items={(entities.world_rules || []).map((r: any) => r.rule_key || r.description)}
        />
        <EntityList
          title="写作约束"
          items={(entities.writing_constraints || []).map(
            (w: any) => `${w.is_hard ? "[硬] " : ""}${w.title || w.body || w.constraint_type}`
          )}
        />
      </div>

      {ctx?.ok && (
        <div className="panel p-3 text-2xs text-text-secondary space-y-1">
          <div className="text-text-disabled">
            Context 预览 · {ctx.assembler_version} · ch{ctx.chapter_no} · {ctx.item_count} items ·{" "}
            {ctx.used_tokens} tok (record-only)
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(ctx.kinds || {}).map(([k, v]) => (
              <span key={k} className="border border-border rounded px-1.5 py-0.5 font-mono">
                {k}:{String(v)}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="text-2xs text-text-disabled">下一动作：{data.next_action || "—"}</div>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: "success" | "brand" }) {
  const accentClass = accent === "success"
    ? "border-success/25 hover:border-success/40"
    : accent === "brand"
    ? "border-brand/25 hover:border-brand/40"
    : "hover:border-border-strong";
  return (
    <div className={`panel-elevated rounded-md px-3 py-2 transition-colors duration-150 ${accentClass}`}>
      <div className="text-2xs text-text-disabled">{label}</div>
      <div className={`text-sm mt-0.5 font-mono ${accent === "success" ? "text-emerald-300" : accent === "brand" ? "text-brand-accent" : "text-text-primary"}`}>
        {value}
      </div>
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
    <div className="panel rounded-md p-3 flex items-start gap-2 transition-colors duration-150 hover:border-border-strong">
      <Icon size={14} className="text-brand-accent mt-0.5 shrink-0" />
      <div className="min-w-0">
        <div className="text-2xs text-text-disabled">{label}</div>
        <div className={small ? "text-xs text-text-secondary mt-0.5" : "text-sm text-text-primary font-mono mt-0.5"}>
          {value}
        </div>
      </div>
    </div>
  );
}

function EntityList({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) {
    return (
      <div className="panel p-3">
        <div className="text-2xs text-text-disabled mb-1">{title}</div>
        <div className="text-2xs text-text-tertiary">暂无</div>
      </div>
    );
  }
  return (
    <div className="panel p-3 max-h-40 overflow-auto">
      <div className="text-2xs text-text-disabled mb-1">
        {title}（{items.length}）
      </div>
      <ul className="text-2xs text-text-secondary space-y-0.5">
        {items.slice(0, 10).map((x, i) => (
          <li key={`${i}-${x}`}>{x}</li>
        ))}
      </ul>
    </div>
  );
}
