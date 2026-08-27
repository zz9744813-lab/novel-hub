# 模型能力证据与连接健康

模型“会不会写”与“此刻能不能连上”是两件事，系统分别处理：

- 能力资格：对合成题库执行一次完整评测。证据按模型身份、端点身份、题库完整内容和评测器版本生成内容地址 key；这些输入不变时，后续资格请求零模型调用并复用原始来源 run。
- 上下文认证：用自适应阶梯分别记录声明长度、接口实际接受长度和质量有效长度；同样按内容 key 复用。
- 连接健康：只运行极小的 L1 `OK` 探针。活跃主模型定时探测，开始自动写作前只对超过新鲜度阈值的候选补测。

## 证据失效规则

以下任一变化会使旧证据变为 `stale`，下一次显式评测会创建新来源：

1. provider、model id、模型类型或可识别的上游版本变化；
2. 实际路由端点变化；
3. 题库版本或题目、阈值、grader 配置变化；
4. 评测器 revision 变化；
5. 来源 run 缺失、失败或与证据 key 不一致。

时间流逝和 API key 轮换不会使能力证据失效。管理员仍可在界面确认后强制重测能力或上下文。

低分但完整执行的评测是有效证据：对应角色会被门禁拒绝，普通重试会复用该结论。网关错误、取消、内部异常和不完整执行不是有效证据，也不会覆盖此前成功来源。

## 写作前门禁

自动路由按候选逐一检查：

1. 当前能力证据有效；
2. 目标角色的来源、key 和通过结果有效；
3. 需要长上下文的角色拥有当前上下文证据，且 measured effective window 足够；
4. L1 健康快照未超过 TTL，状态为允许的健康级别；
5. 综合质量分达到角色下限。

某个无关模型不合格不会阻塞另一合格候选；没有候选时，API 返回精确 blocker code。

## 操作入口

- `GET /api/model-setup/evaluation/evidence`：只读查看能力、角色、上下文与独立健康状态。
- `POST /api/model-setup/evaluation/models/{id}/qualify`：复用或排队能力评测。
- `POST /api/model-setup/evaluation/models/{id}/context-certify`：复用或排队上下文认证。
- 上述接口加 `?force=true`：确认后绕过缓存。
- `GET /api/model-setup/evaluation/runs/{run_id}`：轮询任务。
- `POST /api/model-setup/evaluation/runs/{run_id}/cancel`：排队任务立即取消，运行任务在题目或阶梯边界停止。

常用环境变量：

- `MODEL_PREWRITE_HEALTH_TTL_SECONDS`，默认 `300`；
- `MODEL_PREFLIGHT_PROBE_LIMIT`，默认 `12`；
- `MODEL_HEALTH_TICK_LIMIT`，默认 `12`。

题目响应正文不会落库；仅保存分数、grader 结构化细节、错误码、性能指标和响应 SHA-256。目录元数据会递归删除凭证形字段，端点指纹不包含 API key、查询参数、userinfo 或 fragment。
