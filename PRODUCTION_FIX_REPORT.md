# PRODUCTION_FIX_REPORT

Generated: 2026-07-26T13:04:02.292622+00:00
Baseline: ab38611 → this commit
Live: http://107.172.138.14/

## P0-01

- 状态：PASS (code) / NOT VERIFIED (server-side key revoke)
- 修改文件：deploy/.env.example, .gitignore, backend/alembic.ini, SECURITY_ROTATION_REPORT.md, .github/workflows/ci.yml
- 迁移：无
- 测试：git tree example 无 live key；历史仍含旧 blob，需人工轮换 Key
- 真实运行证据：deploy/.env.example 仅占位符；PRIMARY_BASE_URL=http://new-api:3000/v1 容器可达 /models 200
- 未完成项：New-API 控制台撤销旧 Key（需人工）

## P0-02

- 状态：PASS
- 修改文件：backend/app/agents/caller.py
- 迁移：无
- 测试：tests/test_caller.py 通过
- 真实运行证据：真实 call_agent(query_planner) → AgentRun.status=completed, completed_at set, model=deepseek-v4-flash
- 未完成项：无

## P0-03

- 状态：PASS (code path) / NOT VERIFIED (soak)
- 修改文件：caller.py, pipeline.py, chapter_planner.py, draft_writer.py, review_agent.py, patch_editor.py, state_extractor.py, retrieval.py, reference_analyzer.py
- 迁移：无
- 测试：签名无 db；单元 33 passed
- 真实运行证据：单次真实 call_agent 完成
- 未完成项：pg_stat_activity 长事务 soak、强制 5 分钟延迟验证

## P0-04

- 状态：PASS (code) / NOT VERIFIED (kill-worker recovery E2E)
- 修改文件：arq_worker.py, tables.py ChapterTask lease 字段, 0004_p0_lease_and_constraints.py
- 迁移：0004 + 线上已 ALTER chapter_tasks lease_*
- 测试：WorkerSettings job_timeout=14400 max_tries=1 max_jobs=1
- 真实运行证据：容器内 WorkerSettings 打印 14400 1 1
- 未完成项：DraftWriter 后强杀 Worker 恢复验收

## P0-05

- 状态：PASS (code + primary attempt) / NOT VERIFIED (forced fallback 3 attempts)
- 修改文件：model_gateway.py AttemptRecord/StreamResult, caller.py 每 attempt 写 route+context
- 迁移：无
- 测试：test_model_gateway.py 通过
- 真实运行证据：真实调用 routes [(1, primary, deepseek-v4-flash)] + packages [(1, model)]
- 未完成项：无效主模型 + 有效 fallback 的 3 attempt 审计

## P0-06

- 状态：PASS (code) / NOT VERIFIED (真实 Patch 章节)
- 修改文件：engine/chapter_finalizer.py, pipeline.py Phase10
- 迁移：无
- 测试：import/compile OK
- 真实运行证据：未构造强制 Patch 章节
- 未完成项：T-04 Patch 一致性真实验收

## P0-07

- 状态：PASS
- 修改文件：deploy/docker-compose.yml 使用 env 注入；startup_checks.py
- 迁移：无
- 测试：容器 PRIMARY_BASE_URL=http://new-api:3000/v1；/models 200
- 真实运行证据：/health/ready → provider ok
- 未完成项：无

## P0-08

- 状态：PASS
- 修改文件：main.py Bearer 中间件；frontend/src/api.ts sessionStorage；App.tsx 登录闸
- 迁移：无
- 测试：无 Token/错 Token → 401；正确 Token → 200
- 真实运行证据：鉴权矩阵见 REAL_E2E；前端 index-CGms5xIT.js / index-DjIAeRFx.css
- 未完成项：浏览器人工点验登录 UI

## P0-09

- 状态：PASS
- 修改文件：main.py lifespan；routes.py ready→get_readiness；startup_checks.py
- 迁移：无
- 测试：ready detail db/provider/bindings=ok；14 bindings
- 真实运行证据：GET /health/ready 200 ready
- 未完成项：缺失绑定时 Worker 拒绝领取的负向用例

## P1

- 状态：NOT STARTED per P0-first rule this round

## 总结

P0 代码与部署主链已落地；真实 Provider 单 Agent + 鉴权/就绪探针已过。第 7 章完整门槛（10 章、强杀恢复、fallback 三 attempt、Patch 一致性）仍有 NOT VERIFIED，不得宣称生产完全可用。
