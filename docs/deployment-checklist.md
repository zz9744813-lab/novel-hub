# Novel Hub 部署验收清单

## 1. 环境变量

确认 `.env` 包含：

- NOVELHUB_PASSWORD
- NOVELHUB_SECRET_KEY
- NOVELHUB_ENCRYPTION_KEY
- NOVELHUB_VAULT_ROOT
- NOVELHUB_BACKUP_ROOT
- NOVELHUB_DB_PATH
- NOVELHUB_APP_ENV=production

提醒：
- 生产环境不能使用默认密码。
- 生产环境不能使用默认 secret。
- Vault、backup、DB 路径需要有写权限。

## 2. 构建

运行：

  python -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  npm install
  npm run build
  python -m compileall app tests
  pytest -q

## 3. 启动

运行：

  uvicorn app.main:app --host 0.0.0.0 --port 8787

或使用 systemd：

  sudo systemctl restart novelhub
  sudo systemctl status novelhub

## 4. 页面验收

登录后检查：

- Dashboard `/`
- 项目详情 `/projects/{project}`
- 编辑器 `/projects/{project}/editor/{filename}`
- 导出 `/export`
- 设置 `/settings`
- Timeline `/projects/{project}/timeline`
- Graph `/projects/{project}/graph`
- Threads `/projects/{project}/threads-board`
- Consistency `/projects/{project}/consistency`

## 5. 写作链路验收

- 新建项目
- 新建章节
- 保存章节
- 刷新后正文仍存在
- 修改 synopsis 后 file_index 不丢失
- Markdown 导出正常
- 快照恢复正常

## 6. 安全验收

- 生产环境 CSRF 启用
- `/api/.*` 不再 CSRF 豁免
- 登录失败限流生效
- AI Key 设置页不回显完整 key
- `.env`、数据库、Vault 不进入 Git

## 7. 回滚

- 保留上一版代码目录或 Git tag
- 保留数据库备份
- 保留 Vault 备份
- systemd 可回滚到上一版 ExecStart 路径
