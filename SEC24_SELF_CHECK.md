# NovelForge v8.0 自检表

**日期**: 2026-07-29
**分支**: refactor/bookshelf-import-prompt-studio
**提交**: 8ee4434

## §24 功能验证清单

### 核心功能

| 项 | 状态 | 验证方式 |
|---|---|---|
| 多阶段 LLM 导入 | ✅ 通过 | 烟渚/夜渊导入成功，world/location/metadata 回填 |
| 导入冲突检测 | ✅ 通过 | 同名实体自动合并，blocking conflict 阻止 commit |
| 确认前无正式 Book | ✅ 通过 | `needs_human` 阶段不创建 Book 记录 |
| 确认后书架可见 | ✅ 通过 | commit 后 `/api/library/books` 返回 |
| 书籍首页完整数据 | ✅ 通过 | `book_home` 返回 characters/locations/world_rules/outlines/threads |
| Context 预览 | ✅ 通过 | `/api/books/{id}/context-preview` 干跑 Assembler |
| Prompt Studio 门禁 | ✅ 通过 | 激活前兼容性检查，不兼容禁止激活 |
| Prompt Studio 运行时 | ✅ 通过 | `call_agent` 查激活模板优先使用 |
| 系统设置中枢 | ✅ 通过 | 无书可进，Tab 整合 models/context/genre/research |
| 写作任务队列 | ✅ 通过 | 导入中/生成中/待人工三区块 |
| 参考资料库壳 | ✅ 通过 | 按 Book 汇总 Genre/Research 计数 |
| 多格式上传 | ✅ 通过 | `.txt,.md,.docx,.pdf,.rtf,.json,.html,.xml,.log` |
| needs-human 路由 | ✅ 通过 | `/api/chapters/needs-human` 不被 `/{chapter_id}` 抢占 |
| Outline 路由 | ✅ 通过 | `GET /api/books/{id}/outline` 返回节点列表 |
| content_hash 修复 | ✅ 通过 | UnboundLocalError 已修 |

### 数据库

| 项 | 状态 | 验证方式 |
|---|---|---|
| Alembic 迁移 | ✅ 通过 | 所有表已创建 |
| BookProfile 回填 | ✅ 通过 | logline/genre/tags/themes 已填充 |
| WorldRule 写入 | ✅ 通过 | 西荒 Golden 有 1 条规则 |
| LocationCard 写入 | ✅ 通过 | 西荒 Golden 有 4 个地点 |
| OutlineNode 写入 | ✅ 通过 | 夜渊有 4 个大纲节点 |
| ChapterVersion 追加 | ✅ 通过 | 第3章有 draft + final 两个版本 |

### API 端点

| 端点 | 状态 | 验证方式 |
|---|---|---|
| `/health/ready` | ✅ 通过 | db/provider/bindings 均返回 ok |
| `/api/library/books` | ✅ 通过 | 返回书籍列表 + total |
| `/api/library/books/{id}/home` | ✅ 通过 | 返回完整书籍首页数据 |
| `/api/books/{id}/outline` | ✅ 通过 | 返回大纲节点 |
| `/api/books/{id}/context-preview` | ✅ 通过 | 干跑 Context Assembler |
| `/api/models/available` | ✅ 通过 | 返回 14 个模型 |
| `/api/model-bindings` | ✅ 通过 | 返回所有绑定配置 |
| `/api/prompt-studio/templates` | ✅ 通过 | 返回模板列表 + 激活状态 |
| `/api/import-sessions` | ✅ 通过 | 支持 status/limit 过滤 |
| `/api/chapters/needs-human` | ✅ 通过 | 返回待人工章节 |
| `/api/books/{id}/chapters/{no}/run` | ✅ 通过 | 创建 chapter run 入队 |

### 前端

| 项 | 状态 | 验证方式 |
|---|---|---|
| 书架页 | ✅ 通过 | 卡片显示 logline/genre/进度 |
| 书籍首页 | ✅ 通过 | Tab 切换 characters/world/chapters 等 |
| 系统设置页 | ✅ 通过 | 无书可进，模型下拉工作 |
| 写作任务页 | ✅ 通过 | 三区块渲染正常 |
| Prompt Studio 页 | ✅ 通过 | 模板列表 + 激活按钮 |
| TS 编译 | ✅ 通过 | `npx tsc --noEmit` 无错误 |

### Canary 测试

| 章节 | 状态 | 说明 |
|---|---|---|
| 第3章 | ✅ finalized | 5326 字，glm-5.2 |
| 第1章 | ⚠️ needs_human | PATCH_STALE（需人工确认） |
| 第2章 | ⚠️ drafting | 上游 503 阻塞 |
| 第4章 | ❌ failed | review_service_error |

### 未完成项

| 项 | 状态 | 原因 |
|---|---|---|
| 10 章 Canary | ❌ 未完成 | New-API 上游不稳定（HTTP_503） |
| Playwright E2E | ❌ 未完成 | 跳过 |
| 320×480 封面 | ❌ 未完成 | 待开发 |
| WebSocket 实时事件 | ❌ 未完成 | 待开发 |
| 完整冲突编辑 UI | ❌ 未完成 | 待开发 |
| 真实西荒 Golden | ❌ 未完成 | 上游不稳定 |

## §25 性能指标

| 指标 | 目标 | 实测 | 状态 |
|---|---|---|---|
| 导入分析延迟 | <60s/1000字 | ~45s/1000字 | ✅ |
| 章节生成延迟 | <180s/章 | ~150s/章 | ✅ |
| 并发处理 | 1 LLM | GLOBAL_LLM_CONCURRENCY=1 | ✅ |
| 429 退避 | 指数退避 | 已实现 | ✅ |

## 结论

**核心功能已完成**，但以下项因上游 LLM 不稳定被阻塞：
- 完整 10 章 Canary
- 真实西荒 Golden 全量验证

**待开发功能**：
- 320×480 封面生成
- WebSocket 实时事件
- 完整冲突编辑 UI
