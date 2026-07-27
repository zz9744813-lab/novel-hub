# AI__.md v3.0 实施记录

> 用户否决备份；**预算只记录不限制**（用户 2026-07-27 明确指令）。

## 已落地

### PR-01～PR-05
- reliability / Outcome / Outbox / Step Runner / 原子 Finalize / Strict Schema

### PR-06 Context 预算（用户改写）
- `context_assembler.py`：逐项 Manifest（kind/source/hash/tokens/required/snapshot）
- `budget_mode=record_only`：**永不**因 overflow 裁剪/拦截/context_overflow
- `overflow_advisory` 仅日志+落库字段
- Token 用 `safe_token_estimate`（非裸 `len//4`）
- `call_agent` manifest 同步记录 used / advisory

### B-08 质量门
- `mechanical_gate.py`：可验证一致性（过短 / meta 泄漏 / 空场景 / forbidden 子串）
- Pipeline `consistency_check` **真实执行**并 checkpoint，不再空跳状态

## 验证
- `BUDGET_RECORD_ONLY_OK`（used>>budget 仍不 exclude）
- `GATE_*` / `ASSEMBLE_OK` / `TOKEN_EST_OK`
- `/health/ready` ready

## 仍未宣称完成
- §14 全量故障注入 + 10 章 Go/No-Go
- 全步骤 checkpoint 覆盖补齐（patch 轮次等）
- 模型能力注册表 / EWMA token 全量

**不得宣称** 12 不变量全绿或无人值守 10 章。
