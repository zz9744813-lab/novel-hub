# Personal Evolution OS

一个以"小时×质量"为底层度量、以"技能等级"为坐标系、以"承诺信用"为约束、由 AI 持续判断的个人成长操作系统。

## 核心理念

- **不做打卡** — 打卡奖励"出现"，但出现 ≠ 成长
- **不做任务管理** — 任务奖励"完成"，但完成琐事 ≠ 成长
- **不做日记** — 日记奖励"记录情绪"，但情绪 ≠ 成长
- **只奖励一件事：可被验证的有效小时**

## 技术栈

| 层级 | 选型 |
|---|---|
| Framework | Next.js 15 (App Router) |
| Language | TypeScript (strict) |
| DB | SQLite + Prisma |
| UI | Tailwind CSS + Radix UI |
| AI | Anthropic SDK (Claude) |
| Validation | Zod |
| Charts | Recharts |

## 快速开始

```bash
# 1. 安装依赖
npm install

# 2. 初始化数据库
npx prisma migrate dev --name init
npm run db:seed

# 3. 配置环境变量
# 编辑 .env，填入你的 ANTHROPIC_API_KEY

# 4. 启动
npm run dev
```

打开 http://localhost:3000 即可。

## 功能模块

### Dashboard (`/`)
每日首屏，包含自然语言录入、今日记录、信用仪表盘、技能卡片、AI 诊断、承诺概览。

### Ledger (`/log`)
所有历史时间记录的查询与编辑。支持按日期查看、单条编辑、补录。

### Skills (`/skills`)
技能档案管理。每个技能有 L0-L8 等级体系，AI 评估升级路径。

### Ladder (`/ladder`)
全局 L0-L8 参照系，技能矩阵一览。

### Commitments (`/commitments`)
承诺创建与结算。AI 现实性审查防止过度承诺，到期自动结算信用分。

### Reports (`/reports`)
AI 生成的每日诊断（判/险/令）和周报。

### Settings (`/settings`)
个人资料、信用流水查询、数据导出。

## AI 端点

| 端点 | 功能 |
|---|---|
| `POST /api/ai/parse-log` | 自然语言日记 → 结构化时间记录 |
| `POST /api/ai/reality-check` | 承诺现实性审查 |
| `POST /api/ai/daily-analysis` | 每日成长诊断 |
| `POST /api/ai/weekly-analysis` | 周报生成 |
| `POST /api/ai/skill-assessment` | 技能等级评估 |

## 核心算法

### 有效小时
```
effective = raw × (category_base + output_bonus + deliberate_bonus + commitment_alignment - context_switch_penalty)
```
AI 给出的 effective_hours 会与公式复算值取较低者，防止 AI 给高分。

### 信用分
- 初始 100 分，EMA 平滑（α=0.25）
- 范围 40-150，S/A/B/C/D 五级
- 承诺完成加分，失败/放弃扣分
- D 级自动进入"信用重建模式"

### 技能等级
- 小时是必要条件，deliverables 是充分条件
- 两者都满足才能升级
- AI 每周扫描并触发晋升通知

## 项目结构

```
app/
├── (dashboard)/          # 仪表盘页面组
│   ├── page.tsx          # Dashboard
│   ├── log/              # 时间台账
│   ├── skills/           # 技能管理
│   ├── ladder/           # L0-L8 参照
│   ├── commitments/      # 承诺管理
│   ├── reports/          # AI 报告
│   └── settings/         # 设置
├── api/                  # REST API
│   ├── ai/               # AI 端点
│   ├── logs/             # 时间记录
│   ├── skills/           # 技能
│   ├── commitments/      # 承诺
│   ├── reports/          # 报告
│   ├── dashboard/        # 仪表盘数据
│   └── credit/           # 信用流水
└── onboarding/           # 新手引导

lib/
├── ai/                   # AI SDK 封装、Prompt 模板、Zod Schema
├── credit/               # 信用分算法
├── skill/                # 技能等级判定
├── time/                 # 有效小时计算、聚合
└── db.ts                 # Prisma Client

components/
├── ui/                   # 基础 UI 组件
├── viz/                  # 数据可视化组件
└── sidebar.tsx           # 侧边栏导航

prisma/
├── schema.prisma         # 数据模型
└── seed.ts               # 默认用户 + L0-L8 标准
```

## 三个关键决策时刻

1. **早晨 · 30 秒**：打开 Dashboard，决定今天主攻哪个技能
2. **晚上 · 90 秒**：自然语言录入今日，AI 拆解 + 判断 + 反馈
3. **周日 · 10 分钟**：复盘上周，设下周承诺（AI 做现实性审查）

## License

MIT
