import { useEffect, useState } from "react";
import { api } from "../../api";
import { agentRoleLabel } from "../../agentLabels";
import { Loader2, CheckCircle2, AlertCircle, Shield } from "lucide-react";

export function PromptStudioPage() {
  const [agents, setAgents] = useState<any[]>([]);
  const [templates, setTemplates] = useState<any[]>([]);
  const [selectedRole, setSelectedRole] = useState<string>("");
  const [selected, setSelected] = useState<any | null>(null);
  const [compat, setCompat] = useState<any | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const a = await api.promptStudio.agents();
    setAgents(a.agents || []);
    const t = await api.promptStudio.templates(selectedRole || undefined);
    setTemplates(t.templates || []);
  };

  useEffect(() => {
    load().catch((e) => setMsg(String(e)));
  }, [selectedRole]);

  const open = async (id: string) => {
    const t = await api.promptStudio.getTemplate(id);
    setSelected(t);
    setCompat(t.compatibility || null);
    try {
      const c = await api.promptStudio.compatibility(id);
      setCompat(c);
    } catch {
      /* keep embedded */
    }
  };

  const seed = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await (api.promptStudio as any).seedDefaults();
      setMsg(`已种子 ${r.created_count || 0} 个草稿模板（跳过 ${ (r.skipped || []).length }）`);
      await load();
    } catch (e: any) {
      setMsg(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const runTest = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const r = await api.promptStudio.test(selected.id);
      setCompat(r.compatibility || null);
      setMsg(
        r.passed
          ? "结构/合同测试通过 — 可以激活"
          : `未通过: ${(r.compatibility?.errors || []).join("; ")}`
      );
      await open(selected.id);
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  const activate = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await api.promptStudio.activate(selected.id);
      setMsg("已激活（旧 active 已归档）");
      await load();
      await open(selected.id);
    } catch (e: any) {
      setMsg(e.message || "激活被门禁拒绝");
    } finally {
      setBusy(false);
    }
  };

  const canActivate =
    compat?.can_activate === true ||
    (compat?.ok === true && selected?.last_test_passed === true);

  return (
    <div className="h-full overflow-hidden flex flex-col p-4 gap-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
            提示词工坊
          </h1>
          <p className="text-xs text-text-tertiary">
            Agent · Contract · 版本 · 测试 · 激活（不兼容 / 未测试禁止激活）
          </p>
        </div>
        <button className="btn text-2xs py-1.5 px-3" disabled={busy} onClick={seed}>
          从内置种子草稿
        </button>
      </div>
      {msg && (
        <div className="text-xs text-text-secondary panel px-3 py-2 flex items-center gap-2">
          {msg.includes("通过") || msg.includes("激活") || msg.includes("种子") ? (
            <CheckCircle2 size={12} className="text-success" />
          ) : (
            <AlertCircle size={12} />
          )}
          {msg}
        </div>
      )}
      <div className="flex-1 grid grid-cols-12 gap-3 min-h-0">
        <div className="col-span-3 panel overflow-auto">
          <div className="px-2 py-1.5 text-2xs text-text-disabled border-b border-border">角色</div>
          <button
            className={`w-full text-left px-2 py-1.5 text-xs ${!selectedRole ? "bg-brand-muted text-brand-accent" : "hover:bg-bg-hover"}`}
            onClick={() => setSelectedRole("")}
          >
            全部
          </button>
          {agents.map((a) => (
            <button
              key={a.agent_role}
              className={`w-full text-left px-2 py-1.5 text-xs ${selectedRole === a.agent_role ? "bg-brand-muted text-brand-accent" : "hover:bg-bg-hover"}`}
              onClick={() => setSelectedRole(a.agent_role)}
            >
              <span>{agentRoleLabel(a.agent_role)}</span>
              {a.has_contract ? (
                <span className="ml-1 text-2xs text-text-disabled font-mono">C</span>
              ) : null}
            </button>
          ))}
        </div>
        <div className="col-span-3 panel overflow-auto">
          <div className="px-2 py-1.5 text-2xs text-text-disabled border-b border-border">模板版本</div>
          {templates.length === 0 ? (
            <div className="p-3 text-2xs text-text-disabled space-y-2">
              <div>暂无 Studio 版本</div>
              <button className="btn text-2xs py-1 px-2" onClick={seed} disabled={busy}>
                种子内置草稿
              </button>
            </div>
          ) : (
            templates.map((t) => (
              <button
                key={t.id}
                onClick={() => open(t.id)}
                className={`w-full text-left px-2 py-2 text-xs border-b border-border/50 hover:bg-bg-hover ${selected?.id === t.id ? "bg-brand-muted" : ""}`}
              >
                <div style={{ fontWeight: 510 }}>{t.name}</div>
                <div className="text-2xs text-text-disabled font-mono">
                  v{t.version} · {t.status}
                  {t.output_contract_key ? ` · ${t.output_contract_key}` : ""}
                </div>
              </button>
            ))
          )}
        </div>
        <div className="col-span-6 panel overflow-auto p-3 space-y-2">
          {!selected ? (
            <div className="text-xs text-text-disabled p-6">选择左侧模板查看详情</div>
          ) : (
            <>
              <div className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
                {selected.name}
              </div>
              <div className="text-2xs text-text-disabled font-mono">
                {selected.agent_role} · v{selected.version} · {selected.status} · test=
                {String(selected.last_test_passed)} · contract=
                {selected.output_contract_key || "—"}
              </div>

              {compat && (
                <div
                  className={`panel p-2 text-2xs space-y-1 ${
                    compat.ok ? "border-emerald-500/30" : "border-amber-500/40"
                  }`}
                >
                  <div className="flex items-center gap-1 text-text-secondary">
                    <Shield size={11} />
                    合同门禁 {compat.ok ? "通过" : "未通过"} · can_activate=
                    {String(compat.can_activate)}
                  </div>
                  {(compat.errors || []).map((e: string) => (
                    <div key={e} className="text-red-300">
                      ✗ {e}
                    </div>
                  ))}
                  {(compat.warnings || []).map((w: string) => (
                    <div key={w} className="text-amber-300/90">
                      ! {w}
                    </div>
                  ))}
                  {compat.ok && !(compat.errors || []).length && (
                    <div className="text-emerald-300/90">变量与合同结构检查 OK</div>
                  )}
                </div>
              )}

              <div>
                <div className="text-2xs text-text-disabled mb-1">System</div>
                <pre className="text-2xs bg-bg-canvas border border-border rounded p-2 max-h-40 overflow-auto whitespace-pre-wrap">
                  {selected.system_prompt || "（空）"}
                </pre>
              </div>
              <div>
                <div className="text-2xs text-text-disabled mb-1">User Template</div>
                <pre className="text-2xs bg-bg-canvas border border-border rounded p-2 max-h-40 overflow-auto whitespace-pre-wrap">
                  {selected.user_prompt_template || "（空）"}
                </pre>
              </div>
              <div className="flex gap-2 flex-wrap">
                <button className="btn-primary text-2xs py-1.5 px-3" disabled={busy} onClick={runTest}>
                  {busy ? <Loader2 size={11} className="animate-spin" /> : null}
                  结构/合同测试
                </button>
                <button
                  className="btn text-2xs py-1.5 px-3"
                  disabled={busy || !canActivate}
                  onClick={activate}
                  title={!canActivate ? "需先通过测试且无合同错误" : "激活此版本"}
                >
                  激活
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
