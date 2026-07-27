# AI__.md v3.0 实施记录（相对基线 da1250b，当前生产提交见 git）

> 用户否决备份功能：本规格落地中**不包含**备份/恢复演练实现。

## 已落地（本轮）

### PR-01 状态与数据库骨架
- `backend/alembic/versions/0006_pipeline_reliability.py` → 已 migrate 到 head
- 表：`chapter_runs` / `chapter_step_runs` / `chapter_dispatch_outbox` / `chapter_state_events`（既有）
- 列：`chapters.active_run_id`、`state_version`…；`chapter_versions.version_kind/content_hash/finalization_key`；`agent_runs.chapter_run_id/step_run_id/error_code`
- `backend/app/services/chapter_state_service.py`（对外服务 API）
- `backend/app/engine/state_transition.py`（实际 CAS + 事件）

### PR-02 Run / Outbox / Worker Outcome
- `backend/app/engine/outcomes.py`：`PipelineOutcome` + `PipelineResult`
- `execute_pipeline()` **禁止裸 return**，全部返回强类型 Outcome
- `arq_worker.py` 按 Outcome 映射 Task/Run 状态；**FINALIZED 前复查 final 指针**
- `backend/app/workers/outbox_dispatcher.py` + worker cron `outbox_tick`（每 30s）
- `POST .../run`：同事务写 ChapterRun + Outbox；`Idempotency-Key`；活动 Run 冲突 409
- `GET /api/chapter-runs/{run_id}`

### 缺陷修复（部分）
| ID | 状态 |
|---|---|
| B-01 Task 无条件 completed | **已修** Outcome 映射 |
| B-02 状态入口 | **部分** pipeline/API 主路径走 transition；finalizer 仍有直接写（后续 PR-04） |
| B-05 版本覆盖 | **部分** draft 改为 append-only + supersede draft scenes |
| B-07 soft-pass finalize | **已修**（此前已 fail-closed，保持） |
| B-10 Patch 首段回退 | **已修** `PATCH_STALE` 零修改 |
| B-11 入队吞异常 | **已修** Outbox + dispatcher |
| B-13 读/导出草稿 | **已修** 默认 final only |
| B-16 孤儿 AgentRun | **已有** reconciler（此前） |
| B-06 Canon 原子 | **部分** L1 不再写死 version=1；Extractor 仍先写库（完整原子 Finalize 待 PR-04） |
| B-03/04/08/09/12/14/15 | **未完** Step Runner / 严格 Schema / Lease CAS 等 |

## 验证证据
- `/health/ready` → ready
- Alembic `0006_pipeline_reliability (head)`
- 容器内 `PATCH_STALE_OK`；`PipelineOutcome.FINALIZED`
- 导出 finalized 书 `HTTP 200` ~27KB；无 finalized 书 404
- Worker 注册 `run_chapter_pipeline, outbox_tick, cron:outbox_tick`

## 明确未做（下一刀）
1. 完整 Step Runner + input_hash Checkpoint（PR-03）
2. Extractor 候选化 + 单事务 Finalize/Canon（PR-04 完整）
3. Pydantic contracts + response_format 全角色（PR-05）
4. Context 硬预算（PR-06）
5. Pause 在 Step 边界生效的完整控制面（B-12 深化）
6. 规格 §14 全套测试与 10 章 Go/No-Go

**不得宣称** 12 条不变量全绿或可无人值守 10 章。
