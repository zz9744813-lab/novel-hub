import { useEffect, useState } from "react";
import { api, ModelBinding, ModelChangeLogEntry, AvailableModel } from "../api";
import { Cpu, History, Save, RefreshCw } from "lucide-react";
import { agentRoleLabel } from "../agentLabels";

export function ModelBindingPanel() {
  const [bindings, setBindings] = useState<ModelBinding[]>([]);
  const [logs, setLogs] = useState<ModelChangeLogEntry[]>([]);
  const [available, setAvailable] = useState<AvailableModel[]>([]);
  const [modelSource, setModelSource] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Record<string, Partial<ModelBinding> & { reason?: string }>>({});
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [b, l, a] = await Promise.all([
        api.models.list(),
        api.models.changeLog(),
        api.models.available().catch(() => ({ models: [] as AvailableModel[], count: 0, source: "" })),
      ]);
      setBindings(b);
      setLogs(l);
      setAvailable(a.models || []);
      setModelSource(a.source || "");
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
      setMsg(`已更新 ${agentRoleLabel(b.agent_role)}`);
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

  const modelOptions = (current: string | null | undefined) => {
    const ids = available.map((m) => m.id);
    if (current && !ids.includes(current)) ids.unshift(current);
    return ids;
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>模型绑定</h2>
          <p className="text-2xs text-text-disabled font-mono mt-0.5">
            C-21 · 下拉列表自动从 API 拉取
            {modelSource ? ` · ${modelSource}` : ""}
            {available.length ? ` · ${available.length} models` : ""}
          </p>
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
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>全局绑定</span>
          <span className="ml-auto text-2xs font-mono text-text-disabled">{bindings.length}</span>
        </div>

        {loading ? (
          <div className="p-4 text-xs text-text-disabled">加载中...</div>
        ) : (
          <div className="overflow-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-2xs text-text-disabled border-b border-border">
                  <th className="px-3 py-2 font-normal">角色</th>
                  <th className="px-3 py-2 font-normal">提供商</th>
                  <th className="px-3 py-2 font-normal">主模型</th>
                  <th className="px-3 py-2 font-normal">备用模型</th>
                  <th className="px-3 py-2 font-normal">变更原因</th>
                  <th className="px-3 py-2 font-normal">版本</th>
                  <th className="px-3 py-2 font-normal"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {bindings.map((b) => {
                  const e = editing[b.id] || {};
                  const dirty = !!editing[b.id];
                  const primary = e.primary_model ?? b.primary_model;
                  const fallback = e.fallback_model ?? b.fallback_model ?? "";
                  return (
                    <tr key={b.id} className="hover:bg-bg-hover/50">
                      <td className="px-3 py-2 text-text-primary" style={{ fontWeight: 510 }} title={b.agent_role}>
                        {agentRoleLabel(b.agent_role)}
                      </td>
                      <td className="px-3 py-2">
                        <select
                          className="w-28 bg-bg-canvas border border-border rounded px-1.5 py-1 text-2xs font-mono text-text-secondary focus:outline-none focus:border-brand"
                          value={e.provider ?? b.provider}
                          onChange={(ev) => setField(b.id, "provider", ev.target.value)}
                        >
                          {["openrouter", "new-api", "openai", "anthropic", "custom"].map((p) => (
                            <option key={p} value={p}>{p}</option>
                          ))}
                          {(e.provider ?? b.provider) &&
                            !["openrouter", "new-api", "openai", "anthropic", "custom"].includes(e.provider ?? b.provider) && (
                              <option value={e.provider ?? b.provider}>{e.provider ?? b.provider}</option>
                            )}
                        </select>
                      </td>
                      <td className="px-3 py-2">
                        <select
                          className="w-48 max-w-[14rem] bg-bg-canvas border border-border rounded px-1.5 py-1 text-2xs font-mono text-text-secondary focus:outline-none focus:border-brand"
                          value={primary}
                          onChange={(ev) => setField(b.id, "primary_model", ev.target.value)}
                        >
                          {modelOptions(primary).map((id) => (
                            <option key={id} value={id}>{id}</option>
                          ))}
                        </select>
                      </td>
                      <td className="px-3 py-2">
                        <select
                          className="w-40 max-w-[12rem] bg-bg-canvas border border-border rounded px-1.5 py-1 text-2xs font-mono text-text-secondary focus:outline-none focus:border-brand"
                          value={fallback}
                          onChange={(ev) => setField(b.id, "fallback_model", ev.target.value)}
                        >
                          <option value="">— 无备用 —</option>
                          {modelOptions(fallback || null).map((id) => (
                            <option key={id} value={id}>{id}</option>
                          ))}
                        </select>
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

      <div className="panel-elevated rounded-lg overflow-hidden">
        <div className="px-3 py-2 border-b border-border flex items-center gap-2">
          <History size={13} className="text-brand-accent" />
          <span className="text-xs text-text-secondary" style={{ fontWeight: 510 }}>变更记录</span>
        </div>
        <div className="max-h-48 overflow-auto divide-y divide-border">
          {logs.length === 0 ? (
            <div className="p-3 text-xs text-text-disabled">暂无变更记录</div>
          ) : (
            logs.slice(0, 20).map((l) => (
              <div key={l.id} className="px-3 py-2 text-2xs flex gap-3">
                <span className="text-text-disabled font-mono w-36 shrink-0">{new Date(l.changed_at).toLocaleString()}</span>
                <span className="text-text-primary" style={{ fontWeight: 510 }} title={l.agent_role}>{agentRoleLabel(l.agent_role)}</span>
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
