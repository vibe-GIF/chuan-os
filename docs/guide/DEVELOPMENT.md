# 川流 chuan-os 开发文档

> 本文档是 chuan-os 的完整开发指南，涵盖架构设计、核心概念、目录结构、开发规范和路线图。配套文档：[DECISIONS.md](../plan/DECISIONS.md)（ADR 决策记录）、[ROADMAP.md](../plan/ROADMAP.md)（开发节点）、[REFERENCES.md](../reference/REFERENCES.md)（借鉴来源）。文档总入口见 [docs/README.md](../README.md)。

***

## 1. 项目概述

### 1.1 定位

chuan-os（川流）是一个**本地运行的多部门 AI 班底（A9 家族办公室版）**。单入口「幕僚长」（总公司）按意图路由到 10 个事业部/部门（财富幕僚：秘书/律师/IT/研究/投资/财务/税务/幕僚长；生活贴身：管家/保镖），每个部门下挂岗位 agent 实例，可绑定不同大脑模型和工具集，重活可调用外来 agent（pi/OpenCode/Claude Code）。

**核心目标**：能帮我干活。不追求自己写一个全能 agent，而是做调度层，复用成熟工具。

### 1.2 命名模型（三层解耦）

| 槽位  | 值          | 说明         |
| --- | ---------- | ---------- |
| 代码名 | `chuan-os` | ASCII，给机器看 |
| 展示名 | 川流         | 中文，给人看     |
| 唤醒词 | 小川小川       | 嘴上喊的，语音交互用 |

### 1.3 设计原则

* **采框架，不自研**（ADR-007）: 复用 LangGraph/MCP/Ollama，自身代码控制在 3k-8k 行

* **本地优先**（ADR-003/010）: 能本地推理的不用云端，能本地调用的不用 Docker

* **语音为最终目标**（ADR-011）: TUI 仅调试用，日常使用靠语音全双工

* **渐进式迁移**（ADR-012/013）: 架构改进逐步拆分，不一次性重写

* **封驳安全闸**（ADR-008）: 工具执行前必须过 guard 审核，无放行则无执行

***

## 2. 整体架构

### 2.1 六层架构

```
┌─────────────────────────────────────────────────────┐
│ L5 接入层  CLI │ TUI(调试) │ 语音(最终) │ HUD │ 微信 │ API │
├─────────────────────────────────────────────────────┤
│ L4 编排层  Gateway 七大组件（幕僚长控制平面）          │
├─────────────────────────────────────────────────────┤
│ L3 部门层  10 部门（SOUL.md 驱动，独立 brain/工具）   │
├─────────────────────────────────────────────────────┤
│ L2 工具层  MCP(filesystem) │ Skill(bash/code) │ sub_agent │
├─────────────────────────────────────────────────────┤
│ L1 大脑层  三档：local(本地推理) / cloud_general / cloud_coding │
├─────────────────────────────────────────────────────┤
│ L0 底座    本机硬件（Windows 11 + RX6800M 12GB）     │
└─────────────────────────────────────────────────────┘
```

### 2.2 Gateway 七大组件（ADR-012）

幕僚长从单一 Supervisor 逐步拆分为七个独立组件，借鉴 OpenClaw Gateway：

| 组件                  | 职责                        | 当前状态       | 目标文件                          |
| ------------------- | ------------------------- | ---------- | ----------------------------- |
| ① Message Router    | 意图解析与路由（显式锁定＞关键词＞本体＞兜底）   | ✅ 已拆分为独立组件 | `gateway/message_router.py`   |
| ② Session Manager   | 会话生命周期、多会话隔离              | ✅ 已拆分为独立组件 | `gateway/session_manager.py`  |
| ③ Agent Spawner     | 动态出生/销毁部门、sub\_agent 按需创建 | ✅ 已拆分为独立组件 | `gateway/agent_spawner.py`    |
| ④ Skill Dispatcher  | 工具注册/调度/权限控制              | ✅ 已拆分为独立组件 | `gateway/skill_dispatcher.py` |
| ⑤ Memory Operations | 三层记忆统一读写接口                | ✅ 已拆分为独立组件 | `gateway/memory_ops.py`       |
| ⑥ Heartbeat         | 健康检查、部门状态监控               | ✅ 已拆分为独立组件 | `gateway/heartbeat.py`        |
| ⑦ Cron              | 定时任务、主动提醒推送               | ✅ 已拆分为独立组件 | `gateway/cron.py`             |

### 2.3 一次请求的数据流

```
用户输入 "帮我看份合同"
    ↓ L5 接入层（CLI/语音/微信）
Gateway.Message Router 解析 → 关键词"合同" → 路由到 lawyer
    ↓
Gateway.Session Manager 获取/创建会话
    ↓
Gateway.Agent Spawner 确保 lawyer 部门已出生
    ↓ L3 部门层
lawyer 部门（brain=cloud_general, tools=[filesystem, contract_review]）
    ↓ L2 工具层
调用 filesystem.read_file("合同.docx")
调用 contract_review（legal_scan handler）→ 发现3个风险点
    ↓ L1 大脑层
BrainRegistry 调用 LLM 生成回复
    ↓
guard 安全闸审核输出
    ↓ L5 接入层
回复用户（文字/语音/微信）
```

***

## 3. 核心概念

### 3.1 幕僚长 vs 部门 vs 岗位 vs sub\_agent

| 概念               | 定义                                         | 调度者          | 例子                           |
| ---------------- | ------------------------------------------ | ------------ | ---------------------------- |
| **部门（persona）**  | 事业部/部门，独立人设/职责/工具/大脑，可大可小（默认一人一岗）          | 幕僚长（总公司 CEO） | 律师、IT、研究                     |
| **岗位（agent 实例）** | 部门下挂的干活实例，可多个（编码岗/分析岗…）；部门 = 项目经理（ADR-014） | 部门持有         | PersonaRole 下的实例             |
| **agent**        | 岗位的执行者（外包工程师），从 agent\_pool 取              | 岗位调度         | 内置 ReAct / prime\_agent / pi |
| **sub\_agent**   | 部门可调用的外部 AI agent，对岗位来说是工具                 | 部门调用         | pi、prime\_agent、OpenCode     |

**关系**：幕僚长（总公司 CEO）→ 部门/事业部（PersonaRole，项目经理）→ 岗位（agent 实例，按复杂度 1:1→1:N）→ sub\_agent（外包重活）

### 3.2 三档大脑（ADR-003）

| 档位              | 用途       | 特点                 | 配置位置        |
| --------------- | -------- | ------------------ | ----------- |
| `local`         | 隐私/重要意图  | 本地 Ollama 推理，数据不出机 | config.yaml |
| `cloud_general` | 通用对话/路由  | 免费/低成本，不占 GPU      | config.yaml |
| `cloud_coding`  | 编程/调试/工程 | 推理能力更强             | config.yaml |

具体模型提供商和型号在 `config.yaml` 配置，不绑定特定厂商（ADR-003 不写死模型）。

### 3.3 三层记忆（借鉴 OpenClaw）

| 层      | 存储                                                | 用途           | 生命周期 |
| ------ | ------------------------------------------------- | ------------ | ---- |
| ① 短期会话 | SqliteSaver                                       | 当前对话上下文      | 会话级  |
| ② 长期检索 | SQLite + FTS5 词法（+N43 sqlite-vec 语义旁路，opt-in 默认关） | 跨会话历史召回      | 永久   |
| ③ 共享黑板 | Obsidian markdown                                 | 跨部门共享信息、用户画像 | 永久   |

### 3.4 guard 安全闸（ADR-008）

「规划→审核→执行」三段式的审核段：

* **中书（规划）**: 幕僚长/部门产出待执行方案

* **门下（封驳关 = guard.py）**: 安全/意图/权限审查，approve 或 reject(reason)

* **尚书（执行）**: 仅 approve 的方案才执行

内置 11 条危险模式检测：rm -rf、format/shutdown、DROP/TRUNCATE、PII 泄露、nmap、curl|bash 等。

***

## 4. 目录结构

```
chuan-os/
├── config/                    # 配置层
│   ├── config.yaml            # 大脑路由、全局开关（routing.default_brain 一处切脑）
│   ├── secrets.yaml           # (gitignored) API Key
│   ├── mcp_servers.yaml       # MCP 注册表
│   └── voices.yaml            # 部门独立 TTS 音色（10 部门，A9）
│
├── personas/                  # 部门层（SOUL.md 目录驱动，ADR-013，10 个）
│   └── <name>/                # 每个部门一个目录
│       ├── SOUL.md            # 人设/职责/说话风格（agent 可自写）
│       ├── config.yaml        # brain/工具权限/sub_agents
│       └── MEMORY.md          # 部门私有记忆（运行时生成）
│
├── agents/                    # 外来 agent（本地直接调用，ADR-010）
│   ├── pi/                    # Pi 编程代理（npm 全局）
│   ├── prime_agent/           # Prime Agent
│   ├── claude_code/           # Claude Code
│   ├── opencode/              # OpenCode
│   ├── example/               # 接入示例
│   └── _README.md             # 接入契约
│
├── skills/                    # 技能层（原子能力）
│   ├── *.yaml                 # 技能定义（bash/weather/web_search/contract_review…）
│   └── handlers/              # Python 回调实现（bash_safe/legal_scan/web_search）
│
├── mcp_servers/               # 自定义 MCP 服务端
│   ├── filesystem_server.py   # 文件系统
│   ├── weather_server.py      # 天气
│   └── opencode_wrapper.py    # OpenCode 包装
│
├── chuan/                     # 核心引擎
│   ├── main.py                # CLI 入口
│   ├── runtime_supervisor.py  # 幕僚长运行时（组装 Gateway + 各组件）
│   ├── role.py                # 岗位类 PersonaRole（规划/调度/汇总，ADR-014）
│   ├── agent_pool.py          # agent 池（常驻 + 动态 spawn）
│   ├── persona_loader.py      # 角色加载器（双格式兼容）
│   ├── orchestrator.py        # 轻量路由（显式锁定/关键词）
│   ├── brains.py              # BrainRegistry 三档大脑
│   ├── guard.py               # 封驳安全闸（ADR-008）
│   ├── memory.py / memory_tools.py / consolidation.py   # 三层记忆 + 会话巩固
│   ├── scheduler.py / scheduler_tools.py                # 管家主动触发
│   ├── team_bus.py / team_state.py                      # 成员直通 + 团队状态落盘
│   ├── external_agents.py     # 外来 agent 加载器
│   ├── gateway/               # Gateway 组件（ADR-012）：
│   │   ├── message_router.py  # ① 意图解析与路由
│   │   ├── session_manager.py # ② 会话生命周期
│   │   ├── agent_spawner.py   # ③ 动态出生/销毁
│   │   ├── skill_dispatcher.py# ④ 工具注册/调度/权限
│   │   ├── memory_ops.py      # ⑤ 三层记忆统一接口
│   │   ├── heartbeat.py       # ⑥ 健康检查
│   │   ├── cron.py            # ⑦ 定时任务
│   │   ├── agent_harness.py   # 后台委派 harness（ADR-016）
│   │   └── supervisor_monitor.py  # 监督者全监控（ADR-018）
│   ├── voice/                 # 语音层（ADR-011）：stt/tts/wake_word/sounds/main
│   ├── tui/                   # Textual 终端界面（app/bridge/theme）
│   ├── channels/              # 多端接入：hud.py（HUD 通道）/ wechat.py（微信）
│   ├── adapters/              # mcp_adapter / skill_loader / sub_agent_registry
│   ├── self_improve/          # GEPA 自改进（ADR-020）
│   └── tools/                 # builtin_tools.py 纯 @tool 兜底
│
├── hud_overlay/               # Flutter 全息 HUD 悬浮层（TCP 17889 驱动）
│   ├── lib/                   # main / agent_overlay / jarvis_overlay / …
│   ├── assets/                # jarvis / ironman 序列帧
│   └── scripts/               # package.bat 一键构建
│
├── data/                      # (gitignored) 运行时数据
│   ├── sessions.db            # SqliteSaver 会话持久化
│   ├── memory_fts.db          # 长期记忆 FTS5 索引
│   ├── memory/                # Obsidian vault（黑板 + 部门记忆）
│   ├── teams/                 # 团队状态落盘
│   └── notes/                 # 会话巩固提炼笔记
│
├── memory_store/              # (gitignored) 预留未实现（faiss/vector_store 旧占位；语义检索走 memory_fts.db vec0，N43）
│
├── docs/                      # 文档中心（docs/README.md 为总入口）
│   ├── guide/                 # 📘 指南：DEVELOPMENT.md（本文档）
│   ├── plan/                  # 🗺️ 方向：ROADMAP.md + DECISIONS.md
│   ├── reference/             # 📚 参考：REFERENCES.md
│   ├── diagrams/              # 🖼️ 图示：architecture.svg / lifecycle.svg
│   └── archive/               # 🗄️ 归档：历史文档
│
├── tests/                     # 单元测试（354 passed / 2 skipped）
├── pyproject.toml             # 依赖管理（pip install -e .）
└── README.md                  # 项目门面（精简入口）
```

***

## 5. 开发规范

### 5.1 薄层原则（ADR-007）

* chuan-os 自身代码控制在 **3k-8k 行**（功能完整上限 <15k）

* 图执行、状态机、工具调用、插件系统等重度能力复用 LangGraph/MCP/Ollama

* 不自研图引擎/插件系统/学习循环

* 杠杆率才是关键：\~5k 行撬动框架的 \~100k+ 行

### 5.2 代码规范

* Python 3.13+，类型注解

* 异步优先（LangGraph async invoke）

* 工具实现必须过 guard 安全闸

* 外来 agent 调用必须 shell=False、超时限制、guard 拦截

### 5.3 测试规范

* 每个核心模块对应 tests/test\_<module>.py

* 关键路径（路由/出生/安全闸）必须有测试

* 当前全量 **354 passed / 2 skipped**（需 API key 的用例跳过）

### 5.4 ADR 流程

重大架构决策记录在 `docs/plan/DECISIONS.md`，编号 ADR-001 起。当前 **18 条 ADR**（ADR-001\~018），详见该文件。

***

## 6. 开发路线

> 完整节点状态、验收标准与依赖关系见 [ROADMAP.md](../plan/ROADMAP.md)（单一事实源），本节仅做速览。

### 已完成（N0–N23）

* **N0–N10 基础骨架**：地基 / 大脑 / 工具+MCP / persona 出生 / 幕僚长 / 封驳关 / 记忆 / 外来 agent / 管家 / 端到端 / 测试文档

* **N11–N16 能干活→能记住→能说话**：启动修复+基础工具 / 会话持久化+sub\_agent / 三层记忆 / SOUL.md 角色驱动 / 语音交互闭环 / 角色独立音色+音效

* **N17–N20 能进化**：TUI 终端界面 / Gateway 组件拆分 / 岗位化并行+微信接入 / GEPA 自改进

* **N21–N23 增强层**：后台委派 harness（ADR-016）/ MCP 管理面板（ADR-017）/ 监督者全监控 + HUD 可视化（ADR-018）

全量测试 **354 passed / 2 skipped**。

### 挂账（P5，未做）

* 手机 PWA 接入

详细节点和验收标准见 [ROADMAP.md](../plan/ROADMAP.md)。

***

## 7. 常见问题

### Q: 为什么不用 Docker？

A: Docker 启动慢、资源占用高、Windows 下 WSL2 兼容性问题多。本地能直接运行的 agent（pi/OpenCode/Claude Code）一律 subprocess 调用。仅 Linux-only agent 才用 Docker（ADR-010）。

### Q: 为什么用 LangGraph 而不是自研 agent 循环？

A: LangGraph 提供成熟的 Supervisor 多 agent 编排、checkpointer 持久化、post\_model\_hook 等能力，自研需要重写 ReAct 循环、工具调用、会话管理等，工作量大且容易出 bug。采框架符合薄层原则（ADR-005/007）。

### Q: 部门和 agent 什么关系？

A: 部门 = 岗位 = 项目经理（ADR-014），只做规划/调度/汇总；agent 是岗位的"执行者"（外包工程师），从 agent\_pool 取，按任务复杂度选择（简单→内置 ReAct，中等→prime，重型→pi/OpenCode/Claude Code）。岗位化 1:N 迁移：N37（ADR-032）岗位可持有 N 个 agent 实例 + 多会话并行不串扰；N38（ADR-033）1:N 默认启用（并行 auto 子任务各配独立 worker）；N39（ADR-034）按实例配置工具/模型/记忆（`RoleAgentConfig`）；N40（ADR-035）config.yaml `role_instances` 声明式按复杂度选实例（simple/medium/heavy → 实例，部门可覆盖，改配置即生效）。

### Q: 最终是语音还是 TUI？

A: 语音是最终日常交互方式（ADR-011），TUI 仅用于开发调试和复杂操作。CLI 是最基础的开发入口。

### Q: 为什么不直接用 OpenClaw/Jarvis？

A: chuan-os 的核心价值是"多部门班底调度"——幕僚长路由到不同职责的部门，每个部门可调用不同的外来 agent。OpenClaw 偏单 agent，Jarvis 偏自改进单 agent。chuan-os 做编排层，复用它们的能力。

### Q: 启动报错怎么修？

A: 旧版 `runtime_supervisor.py` 曾调用未实现的 `_create_call_agent_tool()`（sub\_agent 功能残留），该调用已随 Gateway 重构删除——`wake_up()` 现在委托 `agent_spawner.spawn()` 出生岗位，不再存在此方法。当前版本可直接 `python -m chuan.main` 启动，无启动报错（见 ROADMAP「已知遗留」已解决记录）。

***

## 8. 借鉴来源

详见 [REFERENCES.md](../reference/REFERENCES.md)，核心借鉴：

* **OpenClaw**: Gateway 七大组件、三层记忆、SOUL.md 驱动、微信接入

* **Jarvis**: 语音交互闭环、GEPA 自改进、Obsidian RAG

* **assistant-x-openclaw**: 多助手音色、语音素材、Flutter 悬浮层

* **pi / OpenCode / Claude Code**: 外来 agent 接入、TUI 设计参考

* **LangGraph**: 多 agent 编排、ReAct agent、checkpointer

* **Edict**: 封驳关（门下省事前审核）

