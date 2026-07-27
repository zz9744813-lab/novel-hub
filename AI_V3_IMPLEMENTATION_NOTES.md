# AI__.md v3.0 实施记录

> 用户否决备份：不包含备份/恢复演练。

## 已落地

### PR-01 / PR-02
- reliability 表 0006；Run/Outbox；Typed Outcome；final-only 读导出；Patch CAS

### PR-03 Step Runner
- `step_runner.py` input_hash 复用 + Run lease CAS
- Pipeline：query_plan / chapter_plan / draft_scene / review / canon_extract

### PR-04 Atomic Finalize
- Extractor 只产候选；Finalizer 单事务 Finalize+Canon；finalization_key 幂等；禁多场景压扁

### PR-05 Strict Schema（本轮）
- `backend/app/contracts/agents.py`：Pydantic v2 `extra=forbid` + `strict=True`
- 角色契约：review / chapter_planner / patch / state_extractor / query_planner / evidence_ranker / outline_parser / drift_audit
- `call_agent`：`response_format=json_schema`（schema 来自 `model_json_schema()`）
- `full_pipeline_async`：解析后 **Pydantic 校验 fail-closed**
- Schema 失败：**1 次 repair 请求**，仍失败则 blocked
- `normalize_json`：**移除尾逗号软成功**；仅 fence strip + 合法 JSON
- `prompts.PROMPTS[*].output_schema` 由 contracts 注入，不再手写分叉

## 验证
- `ROLES_OK` 8 契约
- `VALIDATE_OK`（extra forbid、strict 不强制 coerce、planner scenes min 1）
- `NORMALIZE_OK`（`{"a":1,}` → None）
- `PIPE_GOOD` / `PIPE_EXTRA_BLOCKED` / `PIPE_TRAIL_BLOCKED`
- `/health/ready` ready

## 未完
- PR-06 Context 硬预算
- retrieval/patch 全步骤 checkpoint 深化
- §14 故障注入 + 10 章 Go/No-Go
- B-08 consistency 真实现

**不得宣称** 12 不变量全绿或无人值守 10 章。
