# AI__.md v2.0 落地执行记录（2026-07-27）

> 对照 `/root/.hermes/cache/documents/doc_22a219f61606_AI__.md`  
> 本文件不记录任何 Secret 明文。

## 已落地（可验证）

### P0 BKP-001/002
- 脚本：`deploy/scripts/backup.sh`
- 内容：PostgreSQL `pg_dump -Fc`、New API SQLite 文件副本、books/exports/references 归档 + SHA256
- 现场一次成功：`/root/novelforge/data/backups/20260727T122846Z`（pg dump ~447KB）
- Cron：`15 * * * * /root/novelforge/deploy/scripts/backup.sh >> /var/log/novelforge-backup.log 2>&1`
- 未完成：异地加密备份、恢复演练（BKP-003）

### P0 OPS-001 共享网络声明
- NovelForge `deploy/docker-compose.yml`：`networks.internal.name: novelforge_internal`
- New API `docker-compose.yml`：`networks.novel_gateway.external: true` → `novelforge_internal`
- 现场：`new-api` 同时在 `new-api_default` + `novelforge_internal`；worker 可解析 `http://new-api:3000`（401 为鉴权，非网络）

### P1 QA-001 移除 finalize soft-pass
- `pipeline.py`：`SOFT_PASS` 已清除；Review 服务失败 / 空失败 → `failed`；Patch 两轮后仍不通过 → `needs_human`
- 容器内校验：`SOFT False` / `FAIL True`

### P1 CORE-001/002 State Transition
- 新模块：`app/engine/state_transition.py`（`transition_chapter` + FOR UPDATE + state_version + event）
- 表：`chapter_state_events`；`chapters.state_version/state_changed_at/last_transition_reason`
- Alembic：`0005_chapter_state_events`（现场因 0004 漂移手工 DDL 后标记 version）
- pipeline 状态写入走 `transition_chapter`；finalizer 在同事务写 event
- 校验：`FINALIZED -> PLANNING` 抛 `IllegalTransitionError`

### P1 LLM-003 JSON Schema 透传
- `call_agent`：结构化角色构建 `response_format.json_schema`
- `model_gateway.stream_*`：payload 附带 `response_format`
- 容器内：`RF True`

## 明确未做（文档仍要求）
- SEC：凭据轮换、SSH key-only、HTTPS、关闭 3000 公网（需人工/域名）
- OPS-002：镜像 Digest 固定
- CORE-004 Step Checkpoint、Agent Run Reconciler
- Model Capability Registry / 成本账 / Token 校准
- 10 章连续耐久验收

## 部署
- 重建 api/worker 镜像并 force-recreate；`/health/ready` → db/provider/bindings ok
- 代码推送见 git log（本文件提交时）


## 续作（2026-07-27 第二批）

### BKP-003 恢复演练
- 脚本：`deploy/scripts/restore_drill.sh`
- 结果：`RESULT=PASS` — public_tables=50, books=2, finalized=2, versions=4, sqlite_integrity=ok
- 报告：`data/backups/20260727T122846Z/RESTORE_DRILL.txt`
- Cron：每月 1 日 04:30 UTC

### CORE-005 孤儿 Run 回收
- 模块：`app/engine/reconciler.py`
- Worker/API 启动调用；2 条 21h+ `running` → `abandoned`（现场 count abandoned=2, running=0）

### COST-001 Token 估算
- 模块：`app/token_estimate.py`（角色 P95 倍率 + 1.15）
- 接入：`v74_utils.save_context_package`、`call_agent` budget
- 样例：naive 1000 → review 3450 / query_planner 2070
