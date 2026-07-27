# AI__.md v3.0 实施记录（相对基线 da1250b，当前生产提交见 git）

> 用户否决备份功能：本规格落地中**不包含**备份/恢复演练实现。

## 已落地

### PR-01 状态与数据库骨架
- `backend/alembic/versions/0006_pipeline_reliability.py` → head
- 表：`chapter_runs` / `chapter_step_runs` / `chapter_dispatch_outbox` / `chapter_state_events`
- 列：`chapters.active_run_id`；`chapter_versions.version_kind/content_hash/...`；`agent_runs.chapter_run_id/...`
- `backend/app/services/chapter_state_service.py`
- `backend/app/engine/state_transition.py`

### PR-02 Run / Outbox / Worker Outcome
- `backend/app/engine/outcomes.py`
- `execute_pipeline()` 强类型 Outcome
- Worker 按 Outcome 映射；FINALIZED 前复查 final 指针
- Outbox dispatcher + cron `outbox_tick`
- `POST .../run` 同事务 Run+Outbox；`GET /api/chapter-runs/{run_id}`

### PR-03 Step Runner + Lease CAS
- `backend/app/engine/step_runner.py`
  - `canonical_hash` / `run_step` / input_hash 复用
  - `acquire_run_lease` / `release_run_lease`（B-03 CAS）
  - Step 边界检查 `control_requested`（pause/cancel）
  - 成功输出不可变；同 hash 复用**不**插第二行（兼容 `ux_step_success_by_input`）
- Pipeline 已 checkpoint：`query_plan` / `chapter_plan` / `draft_scene:N` / `review`
- Worker：有 `ChapterRun` 时走 **DB lease**，不再跨 LLM 持有 session advisory lock
- Heartbeat 同时刷新 Task + ChapterRun lease

### 缺陷修复状态
| ID | 状态 |
|---|---|
| B-01 Typed Outcome | **已修** |
| B-03 Lease 跨 LLM | **已修**（Run lease CAS） |
| B-04 Step Checkpoint | **部分**（主 LLM 步骤已接；retrieval/patch/finalize 未全量） |
| B-05 版本覆盖 | **部分** draft append-only |
| B-07 soft-pass | **已修** |
| B-10 Patch CAS | **已修** |
| B-11 Outbox | **已修** |
| B-13 final-only 读/导出 | **已修** |
| B-06 原子 Finalize+Canon | **未完** |
| B-08/09/12/14/15 | **未完** |

## 验证证据（PR-03）
- `/health/ready` ready
- 容器内：`HASH_OK` / `LEASE_CAS_OK` / `REUSE_OK` / `DIFF_INPUT_OK` / `PAUSE_OK` / `ALL_STEP_RUNNER_OK`
- 同 input 第二次 `run_step` 不重跑 execute_fn；不同 input 增加 attempt
- HEAD：`0b85cc8`

## 明确未做
1. 全步骤 Checkpoint（retrieval / patch 循环 / finalize）
2. Extractor 候选化 + 单事务 Finalize/Canon（PR-04）
3. Pydantic contracts + 全角色 response_format（PR-05）
4. Context 硬预算（PR-06）
5. §14 故障注入 + 10 章 Go/No-Go

**不得宣称** 12 条不变量全绿或可无人值守 10 章。
