# Novel Hub

专业级个人小说创作工作台（FastAPI + SQLite + Jinja2 + HTMX + CodeMirror）。

> 正文始终直接读写 Markdown 文件，SQLite 仅保存索引/统计/设置/操作日志。

## 技术栈

- FastAPI
- SQLite
- Jinja2 + HTMX
- CodeMirror 5（CDN）
- systemd（首版不依赖 Docker）

## 核心特性

- 深色默认 + 浅色切换
- Dashboard 写作仪表盘
- Projects 卡片管理 + 新建项目
- Project 详情（总览、章节筛选、新建章节）
- 三栏写作工作台（左章节树 / 中央编辑 / 右元信息）
- Frontmatter 字段完整支持：
  - `title`, `chapter`, `status`, `volume`, `tags`
  - `synopsis`, `notes`, `pov`, `characters`, `locations`, `warnings`, `draft_version`
- 保存前自动备份到 `.novelhub-backups`
- Characters / World 列表与预览
- Export 页面 + TXT 导出
- Settings 页面（路径、状态、Syncthing 提醒）

## 目录结构

```text
app/
  main.py
  static/
    css/app.css
    js/editor.js
    js/ui.js
  templates/
    base.html
    login.html
    dashboard.html
    projects.html
    project_detail.html
    editor.html
    characters.html
    world.html
    export.html
    settings.html
    _save_result.html
    _preview.html
    _note_preview.html
    _export_result.html
deploy/novelhub.service
requirements.txt
.env.example
```

## Vault 目录约定

默认 Vault Root：`/root/ObsidianVault`

```text
/root/ObsidianVault/Novels/MyNovel/
  chapters/
    001-intro.md
  characters/
    hero.md
  world/
    loc-capital.md
```

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 设置密码/密钥
uvicorn app.main:app --host 0.0.0.0 --port 8787 --reload
```

登录：`http://<VPS_IP>:8787/login`

## systemd 部署

```bash
# 假设部署目录 /opt/novel-hub
python3 -m venv /opt/novel-hub/.venv
/opt/novel-hub/.venv/bin/pip install -r /opt/novel-hub/requirements.txt
cp /opt/novel-hub/.env.example /opt/novel-hub/.env

sudo cp /opt/novel-hub/deploy/novelhub.service /etc/systemd/system/novelhub.service
sudo systemctl daemon-reload
sudo systemctl enable --now novelhub
sudo systemctl status novelhub
```

## 配置变量（与代码一致）

- `NOVELHUB_PASSWORD`
- `NOVELHUB_SECRET_KEY`
- `NOVELHUB_VAULT_ROOT`
- `NOVELHUB_BACKUP_ROOT`
- `NOVELHUB_DB_PATH`
- `NOVELHUB_DAILY_GOAL`
- `NOVELHUB_PROJECT_GOAL`

## 安全与同步注意

- 不要提交真实 `.env`
- 不要提交数据库、备份、ObsidianVault、小说正文
- VPS 参与写入 Markdown 时，Syncthing 文件夹应使用 **Send & Receive**

## CDN 说明

本项目使用以下 CDN：
- HTMX: `https://unpkg.com/htmx.org@1.9.12`
- CodeMirror 5: `https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/...`
- Google Fonts (Inter / Noto Serif SC)
