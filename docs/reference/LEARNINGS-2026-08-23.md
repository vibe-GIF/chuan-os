# 学习笔记 —— BaiLongma / Aivy-OS / deepseek-harness 三方对照

> **📚 活跃笔记**：2026-08-23 的竞品调研与架构借鉴（N21/N22/N23 的决策来源）。文中已落地项见 [ROADMAP.md](../plan/ROADMAP.md) 对应节点；**未落地借鉴点**登记在 [ROADMAP.md](../plan/ROADMAP.md)「后续候选（N24+）」清单，为活跃待办。

> 日期：2026-08-23
> 目的：调研两个同类本地 AI Agent 开源项目（BaiLongma 白龙马、Aivy OS 艾薇），以及 deepseek-harness 委派外部 CLI Agent 的原理，从中提炼可借鉴点与 chuan-os 的差距。

---

## 1. 三个项目一句话画像

| 项目 | 技术栈 | 产品定位 | 状态 |
|---|---|---|---|
| **川流 chuan-os** | Python 3.13 + LangGraph + FTS5 + Flutter HUD | 14 角色班底 + 语音/HUD + 微信通道 的本地多智能体 OS | N0–N21 跑完，315 测试通过（N21 后台委派 harness 已落地） |
| **BaiLongma 白龙马** | Node.js/Electron 主进程 + HTTP/SSE/WS 服务 + SQLite + Brain UI | 持续运行主循环的桌面 AI Agent（心跳/自维护/热点感知） | v2.2.113，281 commits，非常活跃 |
| **Aivy OS 艾薇** | Electron 桌面包 + 多模型兼容 + MCP 协议 + 机器绑定加密 | 「数字生命体伴侣」：人格/记忆/设备绑定/自唤醒/多通道统一 | v2.0.28，商业感最强（激活码/安装包） |

参考链接：
- BaiLongma：https://github.com/xiaoyuanda666-ship-it/BaiLongma
- Aivy-OS：https://github.com/Bo1202/Aivy-OS

---

## 2. 对齐项（chuan-os 已有、另外两家也做的）

| 能力 | chuan-os 对应 |
|---|---|
| 本地 SQLite 持久化 + 全文检索（FTS5） | `chuan/memory.py` + `memory_fts.db` |
| 多角色 / persona 目录格式 | `personas/<name>/{config.yaml, SOUL.md, MEMORY.md}`（14 个） |
| 语音交互（ASR/TTS/唤醒词） | `chuan/voice/` + openwakeword + edge-tts + 金属滤镜 |
| 微信通道（企业微信） | `chuan/channels/wechat.py`（ADR-015） |
| 工具系统 + shell 安全围栏 | `skills/bash.yaml` + Guard（危险 pattern + 超时） |
| 调度 / 定时任务 | `chuan/scheduler.py` + cron gateway 组件 |
| 子 Agent 委托 / 班底协作 | LangGraph supervisor + `agent_pool.py` + 外部 agent 适配 |
| 记忆归档（会话提炼） | `chuan/consolidation.py` 异步 worker |
| MCP 接入基础 | `mcp_servers/` + `adapters/mcp_adapter.py` |

结论：三家骨架高度一致（SQLite 记忆 + 工具围栏 + 语音/通道 + 多 agent）。chuan-os 相对优势在 LangGraph DAG + 14 角色班底 + Flutter 全息 HUD + 离线 STT/TTS 这几块更深。

---

## 3. 可直接借鉴（工程量小、效果明显）

1. **Mission 长任务追踪 + 任务看板**（Aivy）：把散对话组织成可追踪项目 + 跨对话持久化记录 + 实时进度。
2. **SCENE PROTOCOL v1（UI 协议）**（BaiLongma）：`core 持 scene → UI 纯投影 → 用户交互以 intent 回流`，含 hello/welcome 握手、caps 能力协商、scene 全量 + patch 增量。
3. **Canvas 飞卡（可视化工作区）+ canvas_inspect**（Aivy）：8 种内容卡片 + 框选交互 + AI 回头读卡内容（按需 RAG）。
4. **Wiki 知识库（主动整理 + 双向引用 + 置信度 + Lint）**（Aivy）：`projects/topics/entities/analysis/sources` 5 类 md + 双向交叉引用 + AI 主动判断「值不值得整理」。
5. **子智能体后台并行（N 分身互不干扰）**（Aivy）：派发后主对话不阻塞，完成逐个异步回推。
6. **流式打断不丢工具**（Aivy）：打断时保留已执行工具结果，只重跑未完成部分。
7. **局域网 HTTPS 安全访问 + 根证书配对口令**（BaiLongma）：`npm run start:lan` 自动生成自签根证书 + 带口令的 HTTPS 地址，iPad/手机接同一个 Brain UI。

---

## 4. 差距较大（技术栈或路线取舍）

| 差距 | 说明 | chuan-os 是否要补 |
|---|---|---|
| Electron 桌面壳 + 托盘 + 自动更新 + 安装包/签名 | 两家都是桌面原生；chuan-os 是 CLI + Flutter 外置 HUD + Python 三件分跑 | 可选；要「双击即用」体验必须上，但会引入 Node 技术栈 |
| 视觉理解（截图/录屏/PDF/表格） | Aivy 基于多模态自动启用；chuan-os 纯文本 + 语音 | 看需求，可接 GLM-4V / Gemini / Qwen-VL |
| 机器绑定加密 / 陌生人距离 / 自动锁屏 | Aivy 灵魂数据加密到硬件指纹；chuan-os 明文 SQLite | 隐私敏感场景再补 |
| 工具市场 / 动态能力发现 | BaiLongma 运行时按消息+任务+UI 信号裁剪工具集 | 小改：skill_loader 加策略层 |
| MCP 管理面板（UI） | Aivy v1.7.3 有 MCP Server 管理 UI；chuan-os 靠改 yaml | 高性价比，加 TUI 面板或 HUD 页 |
| 媒体生成（音乐/视频） | BaiLongma 带 music/ + AI 视频面板 | 非核心 |
| 安装包 + 激活码 + 发布流水线 | 两家都有 NSIS/smoke test/Release | 面向最终用户才需要 |
| 本地资源感知（系统/桌面/软件/SSH/Git/天气/热点） | BaiLongma 启动采集器 | 中等成本 |

---

## 5. deepseek-harness 原理（rc.8+）—— 委派 Claude-Code 打工

一句话：**dsh 不复刻 Claude-Code 的逻辑，而是通过 Profile-Bundle 桥接插件，本地拉起完整 `claude` CLI 子进程，把编码任务委派给 Claude-Code，Claude-Code 完整跑完自己的 Agent 循环，只把最终结果回传给 dsh 父 Agent。**

```
┌──────────────────────────────────────────────┐
│  dsh 父 Agent (DeepSeek 做路由/调度/对话)    │
│                                              │
│  用户提编码需求                                │
│       │                                      │
│       ▼                                      │
│  Profile-Bundle 桥接插件  ──►  拉起 subprocess  │
│       │                          │           │
│       │                          ▼           │
│       │              claude CLI 子进程        │
│       │            (Claude-Code 原生活力循环) │
│       │              · tool_use              │
│       │              · bash / read / edit    │
│       │              · 思考 + 多轮            │
│       │                          │           │
│       │                          ▼           │
│       │              最终产物 (diff / 文件)   │
│       │                          │           │
│       ◄──────────────────────────┘           │
│  回传给用户 / 做下一步编排                      │
└──────────────────────────────────────────────┘
```

### 三个关键设计决策

| 决策点 | dsh | chuan-os 现状 |
|---|---|---|
| **复用 vs 复刻** | 不复刻 Claude-Code 逻辑，用原生 CLI 子进程「黑盒」跑完整 Agent 循环 | `external_agents.py` + `agents/` 走 `run_claude_code.ps1` 手动拉起，缺父进程编排 + 结果回传闭环 |
| **桥接层** | Profile-Bundle 把 dsh 上下文/文件路径/约束打包成 Claude-Code 能认的 profile 配置注入 | 有 `sub_agent_registry`，缺统一的「任务信封」协议 |
| **结果回收** | 只回收最终结果（不干涉内部 tool call） | LangGraph 细粒度 fan-out，缺「长任务黑盒委托」模式 |

### chuan-os 映射方案（AgentHarness 委派器）

> ✅ **已全部落地（2026-08-23，N21 / ADR-016）**：`chuan/gateway/agent_harness.py`（`AgentHarness`，注意落点在 `gateway/` 而非原计划的 `adapters/`）+ `runtime_supervisor.delegate()`/`delegate_snapshot()` + CLI `/bg` `/tasks` + TUI 同款命令 + HUD 完成回推。`CommandAgent.run` 改 `asyncio.to_thread` 修复事件循环阻塞。全量 315 passed、2 skipped。

- **Step 1** ✅：封装 `chuan/gateway/agent_harness.py`，统一接管 claude_code / opencode / prime_agent / pi，`submit()` 派发即返 task_id + `on_done` 异步回推 + `snapshot()` 看板。
- **Step 2** ✅：`runtime_supervisor` 加 `delegate()`，与现有同步 dispatch 路由并存（校验唤醒 + 常驻池存在性）。
- **Step 3** ✅：TUI 加「后台任务看板」——`/tasks` 命令列表 + 状态栏 `bg N` + 完成自动弹出；尚未升级为独立右侧面板列（仍为对话区文本方式）。

---

## 6. 建议优先级（针对 chuan-os）

| 优先级 | 做什么 | 从哪抄 | 状态 |
|---|---|---|---|
| P0 | 子智能体 fire_and_forget 并行（不阻塞主对话，完成异步回推） | Aivy N 分身 + team_bus | ✅ 已落地（N21/ADR-016，2026-08-23） |
| P0 | MCP Server TUI 管理面板 | Aivy MCP 面板 | ✅ 已落地（N22/ADR-017，2026-08-24） |
| P1 | HUD 通道升级 SCENE 协议（为 PWA 铺路） | BaiLongma SCENE-PROTOCOL | 未动 |
| P1 | Wiki 知识库 5 类目录结构 + 双向链接 + AI 主动整理裁决 | Aivy Wiki 模式 | ✅ 已落地（N24/ADR-019，2026-08-24，见 [LEARNINGS-2026-08-24-knowledge-base.md](./LEARNINGS-2026-08-24-knowledge-base.md)） |
| P2 | Mission 长任务追踪 + 看板面板 | Aivy Mission | 未动 |
| P2 | 局域网 HTTPS + 手机 PWA 接入（P5 挂账） | BaiLongma start:lan | 未动 |
| P3 | Canvas 飞卡、视觉理解、机器绑定加密 | 看预算 | 未动 |

---

## 7. dsh-agent-teams 深度调研（多 agent 团队协议）

> 项目：https://github.com/NanmiCoder/dsh-agent-teams
> 定位：DSH 插件，把单个 DSH 会话变成多 agent 团队的队长。

### 7.1 核心机制

| 机制 | 说明 |
|---|---|
| **Captain-led delegation** | 当前会话创建团队、分配角色、汇总最终结果 |
| **Durable members** | 成员是可恢复的 DSH 子 agent，可被唤醒做后续跟进 |
| **Dependency-aware tasks** | 任务有 `running/idle/ready` 状态，依赖没完成不能认领 |
| **Automatic reuse + safe takeover** | 空闲成员自动认领下一个 ready 任务；重新分配撤销旧 attempt，等旧 worker 静默后再启动 |
| **Direct messaging** | 成员之间直接发 mailbox 消息，不需要队长中继 |
| **Live activity panel** | Web UI 显示进度条、成员列表、任务 DAG 可视化 |

### 7.2 工作流程

```
1. 当前会话创建团队 → 成为队长
2. 队长添加角色成员（基于可恢复子 agent）
3. 目标变成有负责人和依赖关系的任务
4. 共享调度器用 running/idle/ready 状态原子性认领任务
5. 成员更新 attempt_id；重新分配或队长接管撤销旧尝试
6. 队长汇总结果，归档完整团队记录
```

状态存储在 `<workspace>/.agent-teams/`，文件即真相。

### 7.3 chuan-os 映射

| dsh-agent-teams | chuan-os 对应 | 差距 |
|---|---|---|
| Captain | 幕僚长 | 已有路由，缺任务拆分和汇总 |
| Durable members | PersonaRole + AgentPool | 阶段1单选完成，缺可恢复 |
| Dependency-aware tasks | AgentHarness 任务状态机 | ✅ 已落地（N21 状态机升级，2026-08-23）：pending→ready→running→done/failed + depends_on DAG 自动推进 |
| Safe takeover (attempt_id) | `_schedule()` 原子认领 + `claimed_by` | 部分落地（单进程版：ready→running 只一次防重复执行；缺 attempt_id 撤销旧 worker 语义） |
| Direct messaging | 无（全走幕僚长） | 可加黑板共享，成员间直接消息 |
| Live activity panel | TUI `/tasks` + 状态栏 `bg N` | 部分落地（对话区文本，缺独立右侧 DAG 面板） |

---

## 8. NVIDIA AVO — Harness 比模型重要（2026-08-21 发布）

> 论文/博客：https://developer.nvidia.com/blog/nvidia-avo-reaches-100-on-arc-agi-3/
> 核心结论：**同一个 Claude Opus 5，裸跑 30%，加 AVO Harness 后 100%。**

### 8.1 AVO 是什么

**Agentic Variation Operators**——英伟达的通用 Agent Harness 架构。不是新模型，是包裹在模型外面的框架。

### 8.2 三个核心组件

| 组件 | 作用 | chuan-os 对应 |
|---|---|---|
| **持久记忆** | 记住之前试过什么、哪些路走不通，不重复探索 | memory.py + 三层记忆（规划中） |
| **工具使用** | 运行时按需加载工具和技能 | ToolRegistry + MCP + skills |
| **监督者（Supervisor）** | 全程监控 worker 执行轨迹，发现死胡同就 redirect，防止循环浪费 | 幕僚长（只做初始路由，缺全程监控）+ guard.py |

### 8.3 架构

```
主 Worker Agent（干活的，调工具、读写文件）
    ↑ 监控轨迹 + 发现停滞就干预
Supervisor Agent（不干活，只看轨迹，像 CEO）
    ↑ 读取/写入
持久记忆层（之前的尝试、结果、推理）
    ↑ 调用
运行时工具/技能库
```

### 8.4 实际战绩

- **ARC-AGI-3**：183 关全过（Claude Opus 5 裸跑 30%），环境动作比 VISTA 少 12%
- **GPU 内核优化**：连续运行 7 天，探索 500+ 方向，提交 40 个版本，部分配置优于 FlashAttention
- **通用编码 agent**：检查/编辑代码、运行命令

### 8.5 对 chuan-os 的核心启示

1. **方向完全正确**：chuan-os 做的就是 Harness（幕僚长调度 + 角色分工 + 记忆 + 工具），AVO 证明了这是对的
2. **监督者要全程盯着**：chuan-os 幕僚长只做初始路由，AVO 的 Supervisor 是全程监控执行轨迹、发现死胡同就 redirect。这是 chuan-os 岗位阶段2/3需要补的
3. **持久记忆是质变关键**：不是简单的对话历史，是"之前试过什么、哪些路不通"的结构化记忆
4. **模型不是瓶颈**：30%→100% 全靠 Harness，换模型没用

---

## 9. TUI 设计参考（opencode / Claude Code / Hermes）

### 9.1 三家 TUI 对比

| 项目 | 技术栈 | 核心亮点 |
|---|---|---|
| **opencode** | Go + Bubble Tea（Elm 架构） | 可折叠 Sidebar、Footer 状态条、thinking 指示器、主题自定义、SQLite 会话持久化 |
| **Claude Code** | React 组件树 → ANSI 渲染（8万行UI代码） | 全屏备用屏幕模式（像 vim）、背景填充区分消息类型、实时 token 燃料条、389个组件 |
| **Hermes** | React Ink + Node前端 ↔ Python后端（JSON-RPC） | 即时首帧、非阻塞输入（agent没就绪也能打字排队）、模态面板（模型选择/审批）、实时推理流 |

### 9.2 chuan-os TUI 设计（三栏式，体现多角色班底）

```
┌─ 川流 chuan-os v0.1.0 ─────────────────────────────────────┐
│ 角色面板      │  对话区（当前角色）            │  任务面板    │
│              │  ┌────────────────────────┐   │  □ 读README  │
│ [幕僚长] 在线 │  │ 用户: 读一下README      │   │  ■ 写代码 进行│
│ [编程]  忙碌 │  │ 川流> [编程] 调用工具... │   │  □ 跑测试    │
│ [律师]  空闲 │  │ [编程] README内容...     │   │              │
│ [秘书]  空闲 │  │                        │   │  工具调用日志 │
│ [管家]  空闲 │  │                        │   │  read_file ✓ │
│ ...          │  │                        │   │  list_dir  ✓ │
├──────────────┴────────────────────────┴────────────────────┤
│ 输入框: >_                                                    │
├──────────────────────────────────────────────────────────────┤
│ glm-4-flash │ 12.4K/200K │ 编程忙碌中 │ /help 帮助 │ Tab切角色 │
└──────────────────────────────────────────────────────────────┘
```

### 9.3 技术选型：Textual

- Python 原生，和 chuan-os 技术栈一致
- 声明式组件，类似 React
- 支持鼠标、键盘、动画、CSS 样式
- 不选 React Ink/Bubble Tea 的原因：需要 Node.js 或 Go，增加部署复杂度

### 9.4 实现阶段

| 阶段 | 内容 |
|---|---|
| 1 最小可用 | 左侧角色面板 + 中间对话区 + 底部输入框 + 状态栏 |
| 2 体验优化 | 工具调用折叠、代码高亮、Markdown渲染、流式输出 |
| 3 多角色 | 右侧任务面板、任务DAG可视化、角色切换、共享黑板显示 |
| 4 高级 | 模态面板（模型切换/会话管理/审批）、实时token计量、非阻塞输入 |

---

## 10. 技术栈对比与选型思考

### 10.1 各项目技术栈

| 项目 | 技术栈 | 选择原因 |
|---|---|---|
| chuan-os | Python | LangGraph/LangChain/语音/向量库生态全在 Python |
| BaiLongma | Node.js + Electron | 全栈 JS，Electron 桌面 + Brain UI 前端统一语言 |
| Aivy OS | Electron + Python | Electron 做 UI，Python 做 AI/语音/向量后端 |
| DSH | TypeScript + Cordis | DeepSeek 官方，TS 类型安全，Cordis 适合插件化 |
| opencode | Go + Bubble Tea | 单二进制部署，Go 性能好 |
| Claude Code | Node.js + React→ANSI | Anthropic 官方，React 组件模型 |
| Hermes | Python后端 + Node(React Ink)前端 | Python 做 AI 核心，React Ink 做 TUI |

### 10.2 技术栈差异的根本原因

1. **团队背景**：每个人用自己最熟的语言
2. **项目定位**：纯CLI→Go/Python，桌面应用→Electron，插件框架→TS+Cordis
3. **生态依赖**：AI/ML→Python，前端/Web→JS/TS，系统编程→Go
4. **架构选择**：单语言全栈 vs 前后端分离 vs 插件化

### 10.3 chuan-os 选型结论

Python 选对了。核心依赖（LangGraph、语音、向量库）全在 Python。未来如需桌面应用，可学 Aivy OS 加 Electron 壳，但核心引擎保持 Python。

---

## 11. BaiLongma ACI 预判注入（补充）

> BaiLongma 最核心的架构创新，之前文档没展开。

### 11.1 问题

传统 Agent 是串行循环：用户输入→LLM思考→调工具A→等结果→LLM思考→调工具B→等结果→输出。延迟叠加，资源空转。

### 11.2 ACI 核心理念

**在模型开口之前，系统主动预判它需要什么，提前查好，直接注入。**

模型从"主动调工具的猎人"变成"被喂好信息的厨师"。

### 11.3 三类预判场景

| 场景 | 例子 | 置信度来源 |
|---|---|---|
| **语义记忆预判** | "回顾上次聊的TICK"→自动向量检索相关记忆 | 向量相似度 |
| **工具链模式预判** | "写早报"→并行查天气+新闻+日程 | 模式匹配得分 |
| **定时预热注入** | 每天8点自动查天气/新闻/日历，缓存1h | 固定1.0（时间触发） |

### 11.4 约束

只有只读、幂等、低副作用、结果有时效的工具才能预判（write_file/send_message 不行）。

注入器有 1.5s 超时，宁可少注入不能拖慢响应。

### 11.5 chuan-os 映射

幕僚长在路由之前，可以先并行预判：
- 语义记忆检索（相关历史对话）
- 工具链预执行（如"写早报"→并行查天气+日程）
- 预热缓存读取（cron 提前查好的信息）

然后再分配给角色，角色拿到任务时相关信息已经准备好了。

---

## 12. 综合启示与 chuan-os 下一步

### 12.1 三个项目的本质定位

| 项目 | 本质 | 核心差异化 |
|---|---|---|
| **Aivy OS** | 伴侣 | 人格/记忆/情感，"她" |
| **BaiLongma** | 管家 | 强主循环/ACI预判/持续运行 |
| **chuan-os** | 团队 | 多角色分工/幕僚长调度/岗位1:N |

### 12.2 chuan-os 最该抄的（按优先级）

| 优先级 | 抄什么 | 来源 | 状态 |
|---|---|---|---|
| P0 | 子 agent fire_and_forget 并行（不阻塞主对话） | Aivy N分身 + dsh-agent-teams | ✅ 已落地（N21/ADR-016，2026-08-23） |
| P0 | 任务状态机（idle/ready/running/done + 依赖DAG） | dsh-agent-teams + AVO | ✅ 已落地（N21 状态机升级，2026-08-23）：pending→ready→running→done/failed + depends_on 自动推进 + 原子认领 |
| P1 | Supervisor 全程监控（发现死胡同就 redirect） | NVIDIA AVO | ✅ 已落地（N23/ADR-018，2026-08-24） |
| P1 | Wiki 知识库（主动整理 + 双向引用） | Aivy | ✅ 已落地（N24/ADR-019，2026-08-24） |
| P1 | ACI 预判注入（路由前并行预取） | BaiLongma | 未动 |
| P2 | 流式打断不丢工具 | Aivy | 未动 |
| P2 | TUI 三栏式 + 任务面板 | opencode/Claude Code/Hermes | 部分（看板为 /tasks 文本，非独立面板） |
| P3 | Canvas 飞卡、安全加密、局域网HTTPS | 看预算 | 未动 |

### 12.3 核心信念（AVO 验证）

**模型不是瓶颈，Harness 才是。** chuan-os 做的幕僚长调度 + 角色分工 + 记忆 + 工具层，就是 Harness。方向正确，继续深挖。