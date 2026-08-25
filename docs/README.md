# 川流 chuan-os · 文档中心

> 文档按「单一职责 + 同类归组」组织：每个主题只在一处维护，同类文档放同一目录。需要某个信息时从这里导航，不要跨文档复制粘贴。

## 目录结构

```
docs/
├── README.md            # 本文档：导航总入口
├── guide/               # 📘 指南类
│   ├── DEVELOPMENT.md   #   开发指南（架构/概念/目录/规范/FAQ）
│   └── PWA-HUD.md       #   手机 PWA HUD 联调说明（N48 / ADR-043）
├── plan/                # 🗺️ 方向类（路线 + 决策）
│   ├── ROADMAP.md       #   开发路线 N0–N26 节点状态
│   └── DECISIONS.md     #   ADR-001~021 架构决策记录
├── reference/           # 📚 参考类
│   ├── REFERENCES.md    #   借鉴来源与设计出处
│   ├── LEARNINGS-2026-08-23.md # 竞品调研笔记（白龙马/艾薇/dsh，含未落地待办）
│   └── LEARNINGS-2026-08-24-knowledge-base.md # 知识库方法论调研（Karpathy/OSB/7层/记忆机制）
├── diagrams/            # 🖼️ 图示类
│   ├── architecture.svg #   六层架构纵向图
│   └── lifecycle.svg    #   一次请求生命周期
└── archive/             # 🗄️ 归档类（历史快照，不代表现状）
    ├── ARCHITECTURE_V2.md
    ├── HANDOVER-2026-08-23.md
    ├── E2E-TEST-2026-08-22.md
    └── LEARNINGS-2026-08-24.md
```

## 活跃文档（以现状为准）

| 文档 | 主题 | 想知道什么时看这里 |
|---|---|---|
| [guide/DEVELOPMENT.md](./guide/DEVELOPMENT.md) | **开发指南**：架构、核心概念、目录结构、开发规范、FAQ | 怎么开发 / 项目怎么搭的 |
| [guide/PWA-HUD.md](./guide/PWA-HUD.md) | **手机 PWA HUD 联调说明**（N48）：证书、网关启动、手机 HTTPS 接入、协议/API、验收 | 手机怎么连 / PWA 怎么调（N48） |
| [plan/ROADMAP.md](./plan/ROADMAP.md) | **开发路线**：N0–N26 节点状态、验收标准、依赖关系 | 做到哪了 / 下一步做什么 |
| [plan/DECISIONS.md](./plan/DECISIONS.md) | **架构决策记录（ADR-001~021）**：每个重大决策的取舍与理由 | 为什么这么做 |
| [reference/REFERENCES.md](./reference/REFERENCES.md) | **借鉴来源**：OpenClaw/Jarvis/LangGraph/BaiLongma/Aivy/DSH 等项目映射 | 设计出处 / 抄了谁 |
| [reference/LEARNINGS-2026-08-23.md](./reference/LEARNINGS-2026-08-23.md) | **竞品调研笔记**：BaiLongma / Aivy-OS / deepseek-harness 三方对照 + 未落地借鉴点 | 竞品怎么做的 / 还有什么没做 |
| [reference/LEARNINGS-2026-08-24-knowledge-base.md](./reference/LEARNINGS-2026-08-24-knowledge-base.md) | **知识库方法论调研**：Karpathy LLM Wiki / obsidian-second-brain / 姜胡说 7 层 / CC-Codex-Hermes 记忆 + 为什么不用 RAG | 记忆/知识库怎么设计（N24 依据） |

## 入口（根目录）

| 文档 | 主题 |
|---|---|
| [README.md](../README.md) | 项目门面：简介、快速开始、入口列表 |
| [hud_overlay/README.md](../hud_overlay/README.md) | HUD 悬浮层：构建、运行、TCP 协议 |

## 历史归档（docs/archive/）

> 历史快照，保留作决策轨迹参考，**不代表当前状态**。现状一律以上面「活跃文档」为准。

| 文档 | 内容 | 对应时期 |
|---|---|---|
| [ARCHITECTURE_V2.md](./archive/ARCHITECTURE_V2.md) | v2 架构设计稿（多角色并行 + 角色-agent 解耦） | 2026-08 升级前 |
| [HANDOVER-2026-08-23.md](./archive/HANDOVER-2026-08-23.md) | 交接文档（N0–N20，HUD + 免提语音） | 2026-08-23 |
| [E2E-TEST-2026-08-22.md](./archive/E2E-TEST-2026-08-22.md) | 端到端实测报告（岗位协作 + 联网搜索） | 2026-08-22 |
| [LEARNINGS-2026-08-24.md](./archive/LEARNINGS-2026-08-24.md) | 学习笔记（N23 监督者全监控） | 2026-08-24 |
