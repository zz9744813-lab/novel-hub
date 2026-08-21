import { useEffect, useRef, useState } from "react";
import { api, CharacterSummary, CoreAnchor } from "../api";
import {
  Loader2,
  Anchor,
  Lock,
  LockOpen,
  Plus,
  Trash2,
  Check,
  X,
} from "lucide-react";
import clsx from "clsx";

const ANCHOR_TYPES: Array<{ value: string; label: string }> = [
  { value: "value", label: "价值观" },
  { value: "belief_core", label: "核心信念" },
  { value: "goal", label: "核心目标" },
  { value: "identity", label: "身份认同" },
  { value: "principle", label: "原则" },
];

function typeLabel(t: string) {
  return ANCHOR_TYPES.find((x) => x.value === t)?.label || t;
}

function Slider({
  label,
  value,
  onChange,
  disabled,
  accent,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
  accent: string;
}) {
  return (
    <div className="flex items-center gap-2 min-w-0">
      <span className="text-2xs text-text-disabled shrink-0 w-8">{label}</span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="anchor-slider flex-1"
        style={{ ["--thumb-color" as any]: accent }}
      />
      <span className="text-2xs font-mono text-text-tertiary w-7 text-right shrink-0">
        {value.toFixed(2)}
      </span>
    </div>
  );
}

function AnchorRow({
  anchor,
  index,
  onSaved,
}: {
  anchor: CoreAnchor;
  index: number;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState(anchor);
  const [saved, setSaved] = useState(false);
  const timer = useRef<number | null>(null);
  const firstRun = useRef(true);

  useEffect(() => setDraft(anchor), [anchor]);

  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    if (draft.priority === anchor.priority && draft.rigidity === anchor.rigidity) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        await api.coreAnchors.update(anchor.id, {
          priority: draft.priority,
          rigidity: draft.rigidity,
        });
        setSaved(true);
        window.setTimeout(() => setSaved(false), 1200);
        onSaved();
      } catch {
        setDraft(anchor);
      }
    }, 700);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.priority, draft.rigidity]);

  const toggleLock = async () => {
    try {
      await api.coreAnchors.update(anchor.id, { is_locked: !draft.is_locked });
      setDraft({ ...draft, is_locked: !draft.is_locked });
      onSaved();
    } catch {
      /* locked rows reject statement edits only; lock toggle always allowed */
    }
  };

  const retire = async () => {
    try {
      await api.coreAnchors.remove(anchor.id);
      onSaved();
    } catch {
      /* ignore */
    }
  };

  const retired = draft.status === "retired";
  if (retired) return null;

  return (
    <div
      className={clsx(
        "panel px-3.5 py-3 space-y-2.5",
        draft.is_locked && "border-brand/25 bg-brand-muted/20"
      )}
      style={{ animation: `slideUp 0.3s ease-out ${index * 60}ms both` }}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="badge bg-brand-muted text-brand-accent text-2xs font-mono">
          {draft.anchor_code}
        </span>
        <span className="badge bg-bg-surface text-text-tertiary text-2xs">
          {typeLabel(draft.anchor_type)}
        </span>
        {draft.source_kind !== "manual" && (
          <span className="text-2xs text-text-disabled font-mono">via {draft.source_kind}</span>
        )}
        <span className="ml-auto flex items-center gap-1">
          {saved && (
            <span className="flex items-center gap-0.5 text-2xs text-success animate-fade-in">
              <Check size={10} /> 已保存
            </span>
          )}
          <button
            onClick={toggleLock}
            className={clsx(
              "btn-ghost p-1.5 rounded",
              draft.is_locked && "text-brand-accent"
            )}
            title={draft.is_locked ? "解锁（允许编辑陈述）" : "锁定（保护陈述不被改写）"}
          >
            {draft.is_locked ? <Lock size={11} /> : <LockOpen size={11} />}
          </button>
          {!draft.is_locked && (
            <button
              onClick={retire}
              className="btn-ghost p-1.5 rounded hover:text-danger"
              title="停用该锚点"
            >
              <Trash2 size={11} />
            </button>
          )}
        </span>
      </div>
      <p className="text-xs text-text-secondary leading-relaxed">
        {draft.statement}
        {draft.is_locked && (
          <Lock size={9} className="inline ml-1.5 -mt-0.5 text-brand-accent" />
        )}
      </p>
      <div className="flex flex-col sm:flex-row gap-x-6 gap-y-1.5">
        <Slider
          label="优先级"
          value={draft.priority}
          onChange={(v) => setDraft({ ...draft, priority: v })}
          accent="#8b8eff"
        />
        <Slider
          label="刚性"
          value={draft.rigidity}
          onChange={(v) => setDraft({ ...draft, rigidity: v })}
          accent="#d4a574"
        />
      </div>
    </div>
  );
}

function AddAnchorForm({
  bookId,
  characterId,
  onCreated,
  onCancel,
}: {
  bookId: string;
  characterId: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const [type, setType] = useState("value");
  const [statement, setStatement] = useState("");
  const [priority, setPriority] = useState(0.5);
  const [rigidity, setRigidity] = useState(0.5);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!statement.trim()) {
      setErr("陈述不能为空");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.coreAnchors.create(bookId, characterId, {
        anchor_code: code.trim() || `A${Date.now().toString(36).slice(-6)}`,
        anchor_type: type,
        statement: statement.trim(),
        priority,
        rigidity,
      });
      onCreated();
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel px-3.5 py-3.5 space-y-2.5 animate-slide-up" style={{ background: "rgba(107,122,255,0.05)" }}>
      <div className="flex items-center gap-2">
        <Plus size={12} className="text-brand-accent" />
        <span className="text-2xs text-text-primary" style={{ fontWeight: 510 }}>
          新增核心锚点
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        <input
          className="input text-2xs py-1.5 px-2.5 flex-1 min-w-32 font-mono"
          placeholder="锚点代码（如 VAL_TRUTH）"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <select
          className="input text-2xs py-1.5 px-2.5 w-auto"
          value={type}
          onChange={(e) => setType(e.target.value)}
        >
          {ANCHOR_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>
      <textarea
        className="input text-xs py-2 px-2.5 resize-none"
        rows={2}
        placeholder="用一句可检验的陈述描述（如：宁可断粮也不说谎）"
        value={statement}
        onChange={(e) => setStatement(e.target.value)}
      />
      <div className="flex flex-col sm:flex-row gap-x-6 gap-y-1.5">
        <Slider label="优先级" value={priority} onChange={setPriority} accent="#8b8eff" />
        <Slider label="刚性" value={rigidity} onChange={setRigidity} accent="#d4a574" />
      </div>
      {err && <div className="text-2xs text-red-400">{err}</div>}
      <div className="flex items-center gap-2 justify-end">
        <button onClick={onCancel} className="btn-ghost text-2xs py-1 px-2.5">
          取消
        </button>
        <button onClick={submit} disabled={busy} className="btn-primary text-2xs py-1 px-3">
          {busy ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
          创建
        </button>
      </div>
    </div>
  );
}

export function CoreAnchorsEditor({ bookId }: { bookId: string }) {
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [anchors, setAnchors] = useState<CoreAnchor[]>([]);
  const [loadingChars, setLoadingChars] = useState(true);
  const [loadingAnchors, setLoadingAnchors] = useState(false);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    setLoadingChars(true);
    setSelected(null);
    api.characters
      .list(bookId)
      .then((r) => {
        setCharacters(r.characters || []);
        if (r.characters?.length) setSelected(r.characters[0].id);
      })
      .catch(() => setCharacters([]))
      .finally(() => setLoadingChars(false));
  }, [bookId]);

  useEffect(() => {
    if (!selected) return;
    setLoadingAnchors(true);
    api.coreAnchors
      .list(bookId, selected)
      .then((r) => setAnchors(r.anchors || []))
      .catch(() => setAnchors([]))
      .finally(() => setLoadingAnchors(false));
  }, [bookId, selected]);

  const refresh = () => {
    if (!selected) return;
    api.coreAnchors
      .list(bookId, selected)
      .then((r) => setAnchors(r.anchors || []))
      .catch(() => {});
  };

  if (loadingChars) {
    return (
      <div className="panel flex items-center justify-center py-10 gap-2 text-text-tertiary text-xs">
        <Loader2 size={14} className="animate-spin" />
        正在加载角色…
      </div>
    );
  }

  if (characters.length === 0) {
    return (
      <div className="panel flex flex-col items-center py-10 text-text-tertiary">
        <Anchor size={22} className="mb-2 opacity-25" />
        <p className="text-xs">暂无角色卡</p>
        <p className="text-2xs text-text-disabled mt-1">章节定稿后角色会自动从叙事中提取</p>
      </div>
    );
  }

  const activeAnchors = anchors.filter((a) => a.status !== "retired");

  return (
    <div className="space-y-3">
      {/* character selector */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {characters.map((c) => (
          <button
            key={c.id}
            onClick={() => setSelected(c.id)}
            className={clsx(
              "flex items-center gap-1.5 px-2.5 py-1.5 rounded-full border text-2xs transition-all duration-150",
              selected === c.id
                ? "border-brand/50 bg-brand-muted text-brand-accent shadow-glow"
                : "border-border bg-bg-surface/60 text-text-tertiary hover:border-border-strong hover:text-text-secondary"
            )}
          >
            <span style={{ fontWeight: 510 }}>{c.name}</span>
            {c.anchor_count > 0 && (
              <span className="font-mono text-text-disabled">{c.anchor_count}</span>
            )}
          </button>
        ))}
      </div>

      {/* anchors */}
      {loadingAnchors ? (
        <div className="flex items-center justify-center py-10 gap-2 text-text-tertiary text-xs">
          <Loader2 size={14} className="animate-spin" />
          正在读取锚点…
        </div>
      ) : activeAnchors.length === 0 && !adding ? (
        <div className="panel flex flex-col items-center py-8 text-text-tertiary">
          <Anchor size={20} className="mb-2 opacity-25" />
          <p className="text-xs">该角色还没有核心锚点</p>
          <p className="text-2xs text-text-disabled mt-1">锚点定义角色的稳定内核，约束其长期行为逻辑</p>
        </div>
      ) : (
        activeAnchors.map((a, i) => (
          <AnchorRow key={a.id} anchor={a} index={i} onSaved={refresh} />
        ))
      )}

      {adding && selected && (
        <AddAnchorForm
          bookId={bookId}
          characterId={selected}
          onCreated={() => {
            setAdding(false);
            refresh();
          }}
          onCancel={() => setAdding(false)}
        />
      )}

      {!adding && (
        <button
          onClick={() => setAdding(true)}
          className="btn-ghost text-2xs py-1.5 px-3 w-full justify-center border border-dashed border-border hover:border-brand/40 rounded-lg"
        >
          <Plus size={11} />
          添加锚点
        </button>
      )}
    </div>
  );
}
