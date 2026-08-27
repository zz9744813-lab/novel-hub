# 《诸天红颜录》生产与发布手册

这条链路把参考边界、故事资产、连续写作、质量门和服务器发布分开留证。任何一步失败都不得用下一步“看起来正常”覆盖。

## 1. 一次性本地验收

从仓库根目录执行：

```powershell
py -3.11 backend/scripts/production_pack.py validate `
  --pack backend/production_packs/zhutian_hongyanlu/pack.json `
  --reference 'F:\小说\gem\绿\长篇\【精校】琼明神女录.txt'
```

该命令先要求参考文件原始字节的 SHA-256 与生产包声明完全一致，再验证 3 个候选方向、读者契约、8 位角色、12 组关系、世界规则、24 个关键事件、6 卷和 96 章合同，并仅以哈希报告与参考全文连续 16 字重合。参考原文不会写入生产包、提示词或服务器数据。

`build_chapters.py` 是 96 章合同的可重复蓝图；修改后必须重新生成并验证：

```powershell
py -3.11 backend/production_packs/zhutian_hongyanlu/build_chapters.py
py -3.11 -m pytest backend/tests/test_production_pack.py -q
```

## 2. 建立稳定且受限的 VPS 通道

当前第一次引导必须从 VPS 提供商控制台完成，因为未知密码和未授权私钥不能通过重试修复。

本机生成专用密钥，不复用 root 密钥：

```powershell
ssh-keygen -t ed25519 -a 64 -f "$env:USERPROFILE\.ssh\novelforge_ops" -C "novelforge-ops"
```

若提供商的 noVNC 会破坏多行粘贴，使用已经合并到主干的完整 SHA，分别执行下面两行。下载命令刻意不写协议头，全部字符均不需要 Shift，避免 noVNC 粘贴 `&&`、管道或大写字符后卡住修饰键：

```bash
curl --fail --show-error --location --output /tmp/n raw.githubusercontent.com/zz9744813-lab/novel-hub/<40位主干SHA>/deploy/ops/bootstrap-console.sh
bash /tmp/n
```

控制台末尾必须出现 `NOVELFORGE_BOOTSTRAP_OK` 才算成功。该专用脚本内置的只是可公开的 `novelforge-ops` 公钥，不含私钥或密码；它固定下载经审核的三个运维脚本，并从原部署目录迁移 `.env`，可安全重复运行。需要为其他机器安装不同公钥时，也可显式传入 Ed25519 公钥主体作为唯一参数。

也可以手工把公钥和 `deploy/ops` 三个脚本放到临时目录，以 root 执行：

```bash
bash bootstrap-novelops.sh \
  https://github.com/zz9744813-lab/novel-hub.git \
  /root/novelforge_ops.pub
install -m 0600 /root/novelforge.env /srv/novelforge/shared/.env
```

把 `deploy/ops/ssh-config.example` 追加到本机 `~/.ssh/config`，随后验证：

```powershell
ssh novel-hub status
```

`novelops` 使用 `restrict + ForcedCommand`，并由独立的 `Match User novelops` SSHD 配置关闭密码、键盘交互、PTY 和转发；没有交互 shell、SCP 或任意 sudo。引导过程会先执行 `sshd -t`，语法通过后才重载 SSHD。允许的远端命令只有：

```text
status
deploy <40位主干提交SHA>
rollback <已存在的40位主干提交SHA>
logs <web|api|worker|postgres|redis> <1..500>
novel <validate|qualify|install|start|status|audit|export|download>
```

先确认该通道多次可用，再从控制台决定是否关闭 root 远程登录。引导脚本不会替你冒险关闭最后一个入口，也不会修改本机 v2rayN、TUN 或网络栈。

## 3. 按主干 SHA 发布

PR 合并后取得完整 40 位主干 SHA：

```powershell
ssh novel-hub "deploy <merge-commit-sha>"
```

服务器只接受 `origin/main` 上的提交。流程依次执行镜像拉取、独立版本目录、目标机 Compose 配置展开校验、PostgreSQL 就绪检查与迁移前自定义格式逻辑备份、镜像构建、Alembic 升级、配置模型文本握手与版本化资格证据、生产包校验、原子切换、容器启动和 `/health/ready` 验收。模型证据键未变化时资格步骤只写缓存命中审计，模型调用数为 0；只有模型身份、套件或评测器版本变化时才重新评测。首次接管时，若 `/srv/novelforge/current` 是旧目录或旧链接而不是受管的 40 位主干 SHA 链接，切换过程会先将它原子移动到 `/srv/novelforge/legacy/current-<UTC时间>/` 留存，不会覆盖或递归删除。备份保存在 root 专用的 `/srv/novelforge/shared/data/backups/`；Compose 配置无效、模型资格失败或备份失败时都不会切换版本。应用启动失败时 `current` 自动切回上一个版本。数据库不会自动降级或自动恢复备份，因此迁移仍须保持前后版本可兼容，恢复操作必须在提供商控制台人工确认后进行。

资格步骤也可由受限运维账号显式重试：

```bash
ssh novel-hub "novel qualify"
```

它只处理生产角色已经绑定的模型。对于 `/models` 没有模态字段的兼容网关，只有配置模型成功返回非空文本后才会被标记为文本模型；其他未知、图像、视频或嵌入模型不会被探测或提升。

## 4. 启动和监控全书写作

```powershell
ssh novel-hub "novel install"
ssh novel-hub "novel start"
ssh novel-hub "novel status"
ssh novel-hub "logs worker 200"
```

`start` 创建无截止时间、可恢复的自动写作会话。每次自动写作开始前只做轻量连接预检，能力与上下文资格直接复用版本化证据；正文目标从生产包读取为每章 4200–5800 字。每章依次经历场景计划、因果合同、正文、连续性审查、局部返修、机械门、状态提取、实现门和原子定稿。卷内每 10 章形成 L2，卷尾补余段并形成 L3；每 30 章运行带正文分层样本的漂移审计，发布门还会补审不足 30 章的末尾区间 91–96。中断后再次执行 `novel start` 会恢复可恢复会话，或从最近的终态会话之后创建新会话，不要求一次进程独自写完 96 章。

会话只有在 96 章全部定稿、下一章选择器返回 `OUTLINE_EXHAUSTED` 时才算自然完成。`blocked`、`needs_human`、连接失败或时间到都不是全书完成。

## 5. 盲审与导出

服务器先运行确定性门，再运行匿名分层盲审：

```powershell
ssh novel-hub "novel audit"
ssh novel-hub "novel export"
ssh novel-hub "novel download" > .\zhutian-hongyanlu.txt
```

`download` 仍会先验证当前全文对应的通过审计，随后才把 UTF-8 全文写到标准输出；它不开放任意文件读取。下载后应在本机用同一参考文件再跑一次 16 字连续重合检查，最终报告同时记录全文 SHA 与重合扫描结果。

```powershell
py -3.11 backend/scripts/production_pack.py scan-output `
  --manuscript .\zhutian-hongyanlu.txt `
  --reference 'F:\小说\gem\绿\长篇\【精校】琼明神女录.txt'
```

确定性门逐章验证最终版本、哈希、字数、正史场景、场景因果合同、事件与因果边、L1/L2/L3、L4、漂移报告、统计值和写作会话自然结束。盲审只收到每卷开篇/中点/卷尾的匿名正文片段，不收到书名、作者、大纲、参考文本或写作提示。任何 major 以上问题都会拒绝发布。

服务器不会保存参考全文，因此导出后还应在本机做最终残留扫描。`scan-output` 只报告全文 SHA、参考 SHA 与重合片段的哈希，不回显参考原句。

通过的导出文件位于服务器共享目录：

```text
/srv/novelforge/shared/data/exports/zhutian-hongyanlu.txt
```

每次发布与盲审都记录代码 SHA、生产包 SHA、全文 SHA、盲审运行号和持久化审计行。没有这些证据，不得宣称“全本跑通”。
