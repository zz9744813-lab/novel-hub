# Novel Hub

Novel Hub 是一个面向长篇小说创作的本地优先工作台。正文以 Markdown 文件为真实来源，SQLite 负责索引、搜索、统计、实体关系、快照和运行设置。

当前版本按 `NovelHub_refactor_task_list.docx` 的 P0 要求收敛为 v14：默认关闭高风险实验模块，核心编辑链路优先保证稳定、可恢复、可审计。

## 技术栈

- FastAPI + Jinja2 + HTMX
- SQLite / FTS
- CodeMirror 6（ESM importmap）
- Alpine.js
- slowapi 登录限流
- cryptography/Fernet 加密本地 AI 设置
- systemd 部署

## 核心能力

- 项目、章节、实体、搜索、导出、统计与设置页
- 三栏编辑器：章节树 / CodeMirror 6 编辑区 / 元信息侧栏
- Markdown frontmatter 字段：`title`, `chapter`, `status`, `volume`, `tags`, `synopsis`, `notes`, `pov`, `characters`, `locations`, `warnings`, `draft_version`
- 保存前自动快照，强制覆盖会额外创建 `pre_overwrite` 快照
- 保存冲突检测：若外部修改导致 `loaded_mtime` 过期，前端会提示确认后再覆盖
- 单次原子写入：`write_markdown()` 只通过临时文件 + `os.replace()` 落盘
- 移动端编辑器提供只读视图入口
- 登录接口 `5/minute` 限流

## Feature Flags

高级模块默认关闭，避免未配置 AI 或实验视图影响核心写作流程。

```env
NOVELHUB_FEATURE_AI=0
NOVELHUB_FEATURE_AI_CHECK=0
NOVELHUB_FEATURE_GRAPH=0
NOVELHUB_FEATURE_TIMELINE=0
NOVELHUB_FEATURE_SCENES=0
NOVELHUB_FEATURE_THREADS=0
```

开启后对应 UI 与 API 才会暴露。默认关闭时，相关路由返回 `404`。

## 配置

复制 `.env.example` 为 `.env`，至少设置：

```env
NOVELHUB_PASSWORD=<login-password>
NOVELHUB_SECRET_KEY=<random-secret>
NOVELHUB_ENCRYPTION_KEY=<fernet-key-or-random-secret>
NOVELHUB_VAULT_ROOT=/root/ObsidianVault
NOVELHUB_BACKUP_ROOT=/root/ObsidianVault/.novelhub-backups
NOVELHUB_DB_PATH=/opt/novel-hub/data/novelhub.db
NOVELHUB_APP_ENV=development
```

生产环境建议：

```env
NOVELHUB_APP_ENV=production
```

生产模式会拒绝默认密码、默认 secret 和空加密密钥。

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8787 --reload
```

访问：

```text
http://127.0.0.1:8787/login
```

## systemd 部署

```bash
python -m venv /opt/novel-hub/.venv
/opt/novel-hub/.venv/bin/pip install -r /opt/novel-hub/requirements.txt
cp /opt/novel-hub/.env.example /opt/novel-hub/.env

sudo cp /opt/novel-hub/deploy/novelhub.service /etc/systemd/system/novelhub.service
sudo systemctl daemon-reload
sudo systemctl enable --now novelhub
sudo systemctl status novelhub
```

## 安全说明

- 不提交 `.env`、数据库、备份、Vault、真实小说正文
- AI API Key 只存加密值，设置页不会回显完整密钥
- CSRF 中间件目前明确停用，等所有表单和 fetch 调用补齐 token 后再重新启用
- 登录失败会触发速率限制

## 验证

```bash
python -m compileall app tests
python -m pytest tests -v
```

## 目录

```text
app/
  main.py
  static/
    css/app.css
    js/editor.js
    js/ui.js
  templates/
tests/
deploy/
requirements.txt
.env.example
```
