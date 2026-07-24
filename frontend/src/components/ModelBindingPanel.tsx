import { useEffect, useState } from "react";
import { api, ModelBinding, ModelChangeLogEntry } from "../api";
import { Cpu, History, Save, RefreshCw } from "lucide-react";

export function ModelBindingPanel() {
  const [bindings, setBindings] = useState<ModelBinding[]>([]);
  const [logs, setLogs] = useState<ModelChangeLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Record<string, Partial<ModelBinding> & { reason?: string }>>({});
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [b, l] = await Promise.all([api.models.list(), api.models.changeLog()]);
      setBindings(b);
      setLogs(l);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const setField = (id: string, field: string, value: string) => {
    setEditing((prev) => ({
      ...prev,
      [id]: { ...prev[id], [field]: value },
    }));
  };

  const save = async (b: ModelBinding) => {
    const patch = editing[b.id];
    if (!patch) return;
    setError(null);
    setMsg(null);
    try {
      await api.models.update(b.id, {
        primary_model: patch.primary_model ?? b.primary_model,
        fallback_model: patch.fallback_model ?? b.fallback_model ?? undefined,
        provider: patch.provider ?? b.provider,
        reasoning_mode: patch.reasoning_mode ?? b.reasoning_mode,
        reason: patch.reason || "UI update",
      });
      setMsg(`已更新 ${b.agent_role}`);
      setEditing((prev) => {
        const n = { ...prev };
        delete n[b.id];
        return n;
      });
      await load();
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>模型绑定</h2>
          <p className="text-2xs text-text-disabled font-mono mt-0.5">C-21 · agent_model_bindings · 运行时读库不读 .env</p>
        </div>
        <button onClick={load} className="btn-ghost px-2.5 py-1.5 text-xs rounded-md flex items-center gap-1.5">
          <RefreshCw size={12} /> 刷新
        </button>
      </div>

      {error && <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-md px-3 py-2">{error}</div>}
      {msg && <div className="text-xs text-emerald-400 bg-emerald-400/10 border border-emerald-400/20 rounded-md px-3 py-2">{msg}</div>}

      <div className="panel-elevated rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-border flex items-center gap-2">
          <Cpu size={13} className="text-brand-accent" />
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>Global Bindings</span>
          <span className="ml-auto text-2xs font-mono text-text-disabled">{bindings.length}</span>
        </div>

        {loading ? (
          <div className="p-4 text-xs text-text-disabled">加载中...</div>
        ) : (
          <div className="overflow-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-2xs text-text-disabled border-b border-border">
                  <th className="px-3 py-2 font-normal">Agent</th>
                  <th className="px-3 py-2 font-normal">Provider</th>
                  <th className="px-3 py-2 font-normal">Primary</th>
                  <th className="px-3 py-2 font-normal">Fallback</th>
                  <th className="px-3 py-2 font-normal">Reason</th>
                  <th className="px-3 py-2 font-normal">Ver</th>
                  <th className="px-3 py-2 font-normal"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {bindings.map((b) => {
                  const e = editing[b.id] || {};
                  const dirty = !!editing[b.id];
                  return (
                    <tr key={b.id} className="hover:bg-bg-hover/50">
                      <td className="px-3 py-2 text-text-primary" style={{ fontWeight: 510 }}>{b.agent_role}</td>
                      <td className="px-3 py-2">
                        <input
                          className="w-24 bg-bg-canvas border border-border rounded px-1.5 py-1 text-2xs font-mono text-text-secondary focus:outline-none focus:border-brand"
                          value={e.provider ?? b.provider}
                          onChange={(ev) => setField(b.id, "provider", ev.target.value)}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          className="w-44 bg-bg-canvas border border-border rounded px-1.5 py-1 text-2xs font-mono text-text-secondary focus:outline-none focus:border-brand"
                          value={e.primary_model ?? b.primary_model}
                          onChange={(ev) => setField(b.id, "primary_model", ev.target.value)}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          className="w-36 bg-bg-canvas border border-border rounded px-1.5 py-1 text-2xs font-mono text-text-secondary focus:outline-none focus:border-brand"
                          value={e.fallback_model ?? b.fallback_model ?? ""}
                          placeholder="—"
                          onChange={(ev) => setField(b.id, "fallback_model", ev.target.value)}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <input
                          className="w-28 bg-bg-canvas border border-border rounded px-1.5 py-1 text-2xs text-text-secondary focus:outline-none focus:border-brand"
                          value={e.reason ?? ""}
                          placeholder="变更原因"
                          onChange={(ev) => setField(b.id, "reason", ev.target.value)}
                        />
                      </td>
                      <td className="px-3 py-2 font-mono text-2xs text-text-disabled">v{b.version}</td>
                      <td className="px-3 py-2">
                        <button
                          disabled={!dirty}
                          onClick={() => save(b)}
                          className={`flex items-center gap-1 px-2 py-1 rounded text-2xs ${dirty ? "btn-primary" : "text-text-disabled cursor-not-allowed"}`}
                        >
                          <Save size={11} /> 保存
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Change log */}
      <div className="panel-elevated rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-border flex items-center gap-2">
          <History size={13} className="text-brand-accent" />
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>Change Log</span>
        </div>
        <div className="max-h-48 overflow-auto divide-y divide-border">
          {logs.length === 0 ? (
            <div className="p-3 text-xs text-text-disabled">暂无变更记录</div>
          ) : (
            logs.slice(0, 20).map((l) => (
              <div key={l.id} className="px-3 py-2 text-2xs flex gap-3">
                <span className="text-text-disabled font-mono w-36 shrink-0">{new Date(l.changed_at).toLocaleString()}</span>
                <span className="text-text-primary" style={{ fontWeight: 510 }}>{l.agent_role}</span>
                <span className="font-mono text-text-tertiary truncate">
                  {l.old_model || "∅"} → {l.new_model}
                </span>
                <span className="text-text-disabled ml-auto">{l.reason}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
