# AI__.md v3.0 实施记录

> 用户否决备份；**预算只记录不限制**。

## 已落地 PR 链
- PR-01/02 Outcome · Outbox · 0006
- PR-03 Step Runner · lease CAS
- PR-04 原子 Finalize + Extractor 候选
- PR-05 Strict Schema contracts
- PR-06 Manifest **record-only budget**
- B-08 mechanical_gate
- **§14 smoke + INV SQL + 故障片段**
- **Patch 多轮 checkpoint + pause 边界**
- **INV-10 Usage 每 attempt 落库（unknown≠缺席）**
- **PR-07 API/UI**：`/api/chapter-runs/{id}`、`/runs`、`/needs-human`；章节列表 Pause/Resume/详情
- **INV-01 数据修复**：历史 finalized 指针补 `version_kind=final`

## 实测口令
- UNITS: OUTCOME PATCH_STALE PATCH_APPLY SCHEMA_EXTRA TRAIL BUDGET_RECORD NO_SQUASH GATE
- FAULT: PATCH_STALE_OK FINALIZE_FILTER_BAD_EVENT_OK LEASE_NO_TAKEOVER_OK FINAL_PTR_OK ACTIVE_RUN_OK
- API_HANDLERS_OK · /health/ready ok

## 仍未完成 / 不得宣称
- 完整 §14 表（Kill worker 中途、Redis 丢队、410 路由等）未全做
- 模型 Contract Probe 全套
- **10 章 Go/No-Go**
- SEC 人工项；备份不做
- 12 不变量：**部分 SQL 绿，非全量故障证明**
