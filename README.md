# Novel Hub

基于 **FastAPI + SQLite + Jinja2 + HTMX + CodeMirror** 的个人小说创作后台。

## 功能

- Dashboard：总字数、今日新增、最近修改章节、项目列表
- Projects：扫描 `/root/ObsidianVault/Novels` 下项目
- Chapters：展示标题、状态、字数、修改时间
- Editor：网页 Markdown 编辑、保存、预览（HTMX）、自动备份
- Frontmatter：读取/写入 `title/chapter/status/volume/tags`
- Characters / World：读取项目下 `characters/`、`world/`
- Export：合并 `chapters/*.md` 导出 txt
- Backup：保存前备份到 `/root/ObsidianVault/.novelhub-backups/`
- 安全：登录密码来自 `.env`

## 项目结构

```text
app/
  main.py
  templates/
  static/
deploy/novelhub.service
requirements.txt
.env.example
```

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 修改 .env 中密码与密钥
uvicorn app.main:app --reload --host 0.0.0.0 --port 8787
```

访问：`http://<VPS_IP>:8787/login`

## Vault 目录约定

默认读取：`/root/ObsidianVault`

小说目录示例：

```text
/root/ObsidianVault/Novels/MyNovel/
  chapters/
    001.md
    002.md
  characters/
    hero.md
  world/
    map.md
```

## systemd 部署

```bash
sudo cp deploy/novelhub.service /etc/systemd/system/novelhub.service
sudo systemctl daemon-reload
sudo systemctl enable --now novelhub
sudo systemctl status novelhub
```

## 注意事项

- **不要提交真实 `.env`**
- 数据库仅用于索引、统计、设置、操作日志
- 正文始终保存在 Markdown 文件中
