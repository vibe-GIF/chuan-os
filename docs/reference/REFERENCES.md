# 借鉴来源与参考项目

chuan-os 的设计借鉴了多个成熟的开源项目和架构范式。本文档记录每个参考项目的来源、借鉴了什么、以及在 chuan-os 中的对应实现。

---

## 1. OpenClaw（小龙虾）

**来源**: 基于 OpenClaw 的二开项目 `assistant-x-openclaw`（Gitee: rubintry/assistant-x-openclaw）

**核心借鉴**:

| 设计 | OpenClaw 实现 | chuan-os 对应 |
|---|---|---|
| Gateway 中心辐射架构 | Node.js 常驻进程，含 Message Router/Session Manager/Agent Spawner/Skill Dispatcher/Memory Operations/Heartbeat/Cron | `chuan/gateway/` 七大组件（ADR-012） |
| 工作区文件驱动 | SOUL.md/AGENTS.md/USER.md/MEMORY.md 定义 agent 行为 | `personas/<name>/SOUL.md`（ADR-013） |
| 三层记忆系统 | 会话 JSON / SQLite+FTS5+向量 / 工作区 markdown | `chuan/memory.py` + `data/`（短期 SqliteSaver / 长期 FTS5 / 黑板 Obsidian） |
| 微信接入 | clawbot-wechat 插件，扫码登录，远程操控电脑 | `chuan/channels/wechat.py`（✅ N19，企业微信 ADR-015） |
| 子 agent 扩展 | spawn-agent 技能动态创建子 agent | `chuan/adapters/sub_agent_registry.py` |

**本地路径**: `D:\Dev\Active\assistant-x-openclaw\`

---

## 2. Jarvis（自改进 AI 助手）

**来源**: Nous Research 的 Hermes Agent 生态 + 用户前期 Jarvis 项目实践

**核心借鉴**:

| 设计 | Jarvis 实现 | chuan-os 对应 |
|---|---|---|
| 语音交互闭环 | 唤醒词 + STT + LLM + TTS 全双工 | `chuan/voice/`（ADR-011） |
| GEPA 自改进循环 | Generate-Execute-Preserve-Assess，agent 干完活评估沉淀 | `chuan/self_improve/gepa.py`（✅ N20） |
| Obsidian RAG 记忆 | markdown 本地优先，语义召回 | `chuan/memory.py` + `data/memory/`（长期 FTS5） |
| 自动技能创建 | 干完活自动创建新技能 | `chuan/self_improve/skill_creator.py`（未实现） |
| 持久记忆自检索 | agent 对话时自动检索历史记忆 | `chuan/memory.py`（✅ N13，FTS5 全文检索） |

**本地路径**: `D:\Dev\Active\jarvis\`

---

## 3. assistant-x-openclaw（多助手语音助手）

**来源**: Gitee: rubintry/assistant-x-openclaw（OpenClaw + Hermes + Flutter UI 的集成项目）

**核心借鉴**:

| 设计 | 实现 | chuan-os 对应 |
|---|---|---|
| 多助手架构 | jarvis / 林妹妹 / 小奴，每个独立唤醒词/音色/人设 | 角色独立 TTS 音色配置 `config/voices.yaml` |
| 唤醒词文件 | keywords/jarvis.txt 等，自定义唤醒词 | `chuan/voice/wake_word.py` |
| 语音素材 | data/voices/ 下 wake/thinking/success/error 音效 | `data/voices/` |
| Flutter 悬浮层 | Jarvis 环形动画，TCP 通信 | `chuan/channels/hud.py` + `hud_overlay/`（✅ 已接入，TCP 17889） |
| 声纹防欺骗 | anti_spoof.py + enroll_speaker.py | （未来参考） |
| VITS 中文 TTS | tts_vits.py，本地中文语音合成 | `chuan/voice/tts.py`（可选 piper/VITS） |

**本地路径**: `D:\Dev\Active\assistant-x-openclaw\`

---

## 4. Pi（编程 agent）

**来源**: npm 包 `@earendil-works/pi-coding-agent`，v0.84.2

**核心借鉴**:

| 设计 | 实现 | chuan-os 对应 |
|---|---|---|
| 本地直接调用 | npm 全局安装，`pi -p "任务"` 非交互模式 | `agents/pi/` 外来 agent，subprocess 调用 |
| TUI 设计 | Bubble Tea（Go），顶部状态栏+对话区+底部输入 | `chuan/tui/` Textual 实现参考 |
| 自定义 provider | models.json 配置 OpenAI 兼容 API | `C:\Users\JYQ74\.pi\agent\models.json`（智谱配置） |
| 工具调用展示 | 折叠面板显示工具调用和结果 | `chuan/tui/`（✅ N17，事件流 + 命令面板） |

**安装路径**: `C:\Users\JYQ74\AppData\Local\Microsoft\WinGet\Packages\OpenJS.NodeJS.LTS_...\node-v24.19.0-win-x64\node_modules\@earendil-works\pi-coding-agent\`

---

## 5. OpenCode

**来源**: GitHub: sst/opencode，MIT 开源

**核心借鉴**:

| 设计 | 实现 | chuan-os 对应 |
|---|---|---|
| 客户端/服务器解耦 | opencode server（TypeScript+Bun+Hono）+ TUI 客户端 | HTTP API / FastAPI Gateway（规划中，未实现） |
| Plan/Build 双 agent | Plan agent 做计划，Build agent 执行 | 角色 sub_agent 按复杂度选择 |
| DAG 并行编排 | wave-based 并行 agent，最多 5 并发 | `chuan/role.py` 岗位化并行（✅ N19，拓扑分波 + asyncio.gather） |
| 75+ provider 支持 | 模型无关，自动路由 | BrainRegistry 三档（可扩展） |
| 子 agent 调用 | @mention 或 Task tool 调用 subagent | `call_xxx` 工具（ADR-012 Agent Spawner） |

---

## 6. Claude Code

**来源**: Anthropic 官方 CLI 编程工具，v2.1.197

**核心借鉴**:

| 设计 | 实现 | chuan-os 对应 |
|---|---|---|
| 本地直接调用 | npm 全局安装，非交互模式 | `agents/claude_code/` 外来 agent（✅ N21 harness 统一接管） |
| TUI 交互设计 | 顶部状态栏+对话区+工具调用折叠 | `chuan/tui/` 参考 |
| 工具审批机制 | 危险操作需用户确认 | guard.py 封驳关（ADR-008） |
| 会话管理 | 会话持久化、上下文压缩 | Session Manager（ADR-012） |

**安装**: npm 全局安装（淘宝镜像）

---

## 7. LangGraph / LangChain

**来源**: LangChain AI 官方框架

**核心使用**:

| 组件 | 用途 | chuan-os 对应 |
|---|---|---|
| create_supervisor | 幕僚长多 agent 路由 | `chuan/runtime_supervisor.py` |
| create_react_agent | 角色 agent 创建 | `chuan/persona_loader.py` birth() |
| ChatOpenAI / ChatOllama | 模型抽象 | `chuan/brains.py` BrainRegistry |
| InMemorySaver / SqliteSaver | 会话持久化 | AsyncSqliteSaver（✅ N12，重启不丢） |
| post_model_hook | LLM 决策后钩子 | guard.py 封驳关注入 |
| langchain-mcp-adapters | MCP 工具适配 | `chuan/adapters/mcp_adapter.py` |

**版本**: langgraph 1.2.11、langchain-core 1.6.0、langgraph-supervisor 0.0.31

---

## 8. Edict（三省六部范式）

**来源**: GitHub: cft0808/edict，⭐6.9k

**核心借鉴**:

| 设计 | 实现 | chuan-os 对应 |
|---|---|---|
| 封驳机制 | 门下省事前审核，reject 打回重拟 | guard.py 封驳关（ADR-008） |
| 三省六部 | 中书拟案→门下封驳→尚书派发 | 规划→审核→执行三段式 |

---

## 9. BaiLongma（白龙马）

**来源**: GitHub: xiaoyuanda666-ship-it/BaiLongma（v2.2.113，Node.js/Electron，持续运行主循环的桌面 AI Agent）。详细调研笔记见 [LEARNINGS-2026-08-23.md](./LEARNINGS-2026-08-23.md)。

**核心借鉴**:

| 设计 | 实现 | chuan-os 对应 |
|---|---|---|
| SCENE PROTOCOL v1（UI 协议） | `core 持 scene → UI 纯投影 → 用户交互以 intent 回流`，含 hello/welcome 握手、caps 能力协商、scene 全量 + patch 增量 | HUD 通道升级候选（P1，未落地） |
| ACI 预判注入 | 路由前并行预取（热点感知/自维护） | 路由增强（P1，未落地） |
| 局域网 HTTPS 安全访问 | `npm run start:lan` 自动生成自签根证书 + 带口令 HTTPS，手机/iPad 接同一 Brain UI | 手机 PWA 接入（P2，挂账） |
| 工具市场 / 动态能力发现 | 运行时按消息+任务+UI 信号裁剪工具集 | skill_loader 加策略层（小改） |

---

## 10. Aivy OS（艾薇）

**来源**: GitHub: Bo1202/Aivy-OS（v2.0.28，Electron + Python，「数字生命体伴侣」）。详细调研笔记见 [LEARNINGS-2026-08-23.md](./LEARNINGS-2026-08-23.md)。

**核心借鉴**:

| 设计 | 实现 | chuan-os 对应 |
|---|---|---|
| MCP Server 管理 UI | v1.7.3 起可视化 MCP server 连接/工具/错误 + 运行时启停 | `chuan/adapters/mcp_adapter.py` + TUI `/mcp` 面板（✅ N22，ADR-017） |
| 子智能体后台并行 | N 分身互不干扰，派发后主对话不阻塞、完成逐个异步回推 | `chuan/gateway/agent_harness.py` 任务状态机 + DAG（✅ N21，ADR-016） |
| Mission 长任务追踪 + 看板 | 散对话组织成可追踪项目 + 跨对话持久化 + 实时进度 | `/tasks` 看板已落地；跨对话 Mission（P2，未落地） |
| Wiki 知识库 | `projects/topics/entities/analysis/sources` 5 类 md + 双向引用 + AI 主动整理裁决 | `chuan/wiki.py` + `notes/` 5 类目录（✅ N24，ADR-019） |
| Canvas 飞卡 + canvas_inspect | 8 种内容卡片 + 框选交互 + AI 回头读卡（按需 RAG） | 未落地 |
| 流式打断不丢工具 | 打断时保留已执行工具结果，只重跑未完成部分 | 未落地 |

---

## 11. deepseek-harness（DSH）

**来源**: DeepSeek 官方 harness（TypeScript + Cordis，委派 Claude-Code 打工）。详细调研笔记见 [LEARNINGS-2026-08-23.md](./LEARNINGS-2026-08-23.md)。

**核心借鉴**:

| 设计 | 实现 | chuan-os 对应 |
|---|---|---|
| Profile-Bundle 桥接 | 本地拉起完整 `claude` CLI 子进程，黑盒跑完 Agent 循环，只回收最终结果 | `chuan/gateway/agent_harness.py` `submit()` 派发即返 + `on_done` 异步回推（✅ N21，ADR-016） |
| 异步旁路不阻塞 | 长任务后台跑，主对话不干等 | `CommandAgent.run` 改 `asyncio.to_thread`（✅ N21） |
| 状态机 + 依赖 DAG | `pending → ready → running → done/failed`，`depends_on` 链式编排 | `AgentHarness` 状态机升级（✅ N21 补充） |

---

## 12. 借鉴原则

1. **采框架，不自研**（ADR-007）: 图执行、状态机、工具调用等重度能力复用 LangGraph/MCP，不重写框架。
2. **本地优先**: 能本地直接调用的不用 Docker（ADR-010），能本地推理的不用云端（ADR-003 local 档）。
3. **渐进式迁移**: 借鉴的设计不一次性重写，而是逐步迁移（ADR-012 Gateway 拆分、ADR-013 SOUL.md 迁移）。
4. **薄层原则**: chuan-os 自身代码控制在 3k-8k 行，杠杆率才是关键（ADR-007）。

---

## 13. 记忆/知识库方法论（N24）

**来源**: Karpathy LLM Wiki（gist, 2026-04）+ obsidian-second-brain（GitHub）+ 姜胡说 7 层 + Claude Code/Codex/Hermes 记忆机制。完整对照见 [LEARNINGS-2026-08-24-knowledge-base.md](./LEARNINGS-2026-08-24-knowledge-base.md)。

| 借鉴点 | 来源 | chuan-os 对应 |
|---|---|---|
| 5 类目录（sources 只读 + topics/entities/analysis/projects） | Karpathy Raw→Wiki + Aivy | `chuan/wiki.py` + `notes/` 子目录（✅ N24，ADR-019） |
| 实体页改写（同名归并 + deprecated 留痕） | obsidian-second-brain / Karpathy | `Wiki.write`（✅ N24） |
| index.md/log.md 双文件（替代 RAG） | Karpathy | `Wiki._refresh_index`/`_append_log`（✅ N24） |
| reconcile / lint 健康检查 | obsidian-second-brain | `Wiki.reconcile`/`lint`（✅ N24） |
| 蒸馏落 raw 不可变层（防幻觉扩散） | Karpathy | `consolidate_sessions(wiki=...)` → `sources/`（✅ N24） |
| 置信度分级 | obsidian-second-brain / CC | `Memory.remember(confidence=1-5)`（✅ N24） |
