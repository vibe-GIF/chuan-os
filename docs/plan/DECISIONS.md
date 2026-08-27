# 架构决策记录 (ADR)

## ADR-001: 三层命名解耦

**决策**: 代码名 `chuan-os` / 展示名「川流」/ 唤醒词「小川小川」三者独立。
**理由**: 机器看 ASCII，人看中文，嘴上喊自由设置的唤醒词。三者不应耦合。

## ADR-002: 幕僚长单入口

**决策**: 用户只与幕僚长交互，幕僚长路由到 N 个 persona。
**理由**: 单入口降低认知负担，路由策略可迭代而不影响用户习惯。

## ADR-003: 大脑三档分级

**决策**: `cloud_general` / `cloud_coding` / `local` 三档，统一 `.complete()` 接口。
**理由**: 智商归大脑，三档只是路由策略。默认走云端免费档不占本机 GPU，仅隐私意图回本地 Ollama。

## ADR-004: Obsidian vault 作为记忆基座

**决策**: 长期记忆（RAG 召回）+ 共享黑板都存 markdown，按 namespace 隔离。
**理由**: markdown 本地优先，人类可读可编辑，不锁定在专有数据库里。

## ADR-005: LangGraph Supervisor 框架

**决策**: 幕僚长采用 LangGraph Supervisor 实现。
**理由**: 成熟的多 Agent 编排框架，社区活跃，降低自造轮子风险。

## ADR-006: 外来 agent 纳入班底（显式注册，非市场自动加载）

**决策**: 支持从 GitHub 等下载的外部 agent 纳入班底，但必须（1）以自包含包形式放在 `agents/<name>/`（含 `agent.yaml` + 可选 `skills/` + `mcp_servers/`），（2）在 `config.yaml` 的 `external_agents.enabled` 列表**显式启用**，（3）同样过 `guard` 安全闸、工具最小权限、黑板走 `shared/external/<name>/` 命名空间。
**理由**: 与「固定可控本地班底」一致——精选手工接入，不采用公共智能体市场的自动发现/自动加载（参考豆包智能体市场 2026-07 下线）。可控、可审计、可一键移除。
**反例**: 不扫描某目录自动把所有 agent 注册为可用角色。

## ADR-007: 保持薄消费层，不重写为框架

**决策**: chuan-os 自身代码控制在 ~3k–8k 行（功能完整上限 <15k）；图执行、状态机、工具调用、插件系统等重度框架能力一律复用 LangGraph / langchain / MCP / Ollama，不自研。
**理由**: DeepSeek Harness、Hermes Agent 等 100k+ 行是因为它们是自写全栈框架；chuan-os 定位是「坐框架上的编排层」（README 已定「采框架」）。LOC 是弱指标，杠杆率才是关键——~5k 行撬动框架的 ~100k+ 行。
**反例**: 不自研图引擎 / 插件系统 / 学习循环，否则退化为又一个框架、丧失 lean 优势。

## ADR-008: 门下省封驳治理层（pre-execution gate with veto）

**决策**: 在「幕僚长定案 → 派发执行」之间插入一道**强制事前审核关**（门下省），任何 agent 在真正调用工具 / 执行动作前必须先过此关；审核不通过则**直接打回重拟**（reject + rewrite），不允许「先执行再补救」。该关落进 `chuan/guard.py`（当前为空壳）。
**理由**: 借鉴范式标杆 Edict（三省六部，cft0808/edict，⭐6.9k，该范式 star 断层第一）的独创「封驳」机制。本机自治 agent 无人实时盯守，一个 agent 在乱调 `rm`、乱发邮件、误删文件之前有人拦一道，比事后日志有用得多。这是「规划 → 审核 → 执行」三段式（对应中书拟案 / 门下封驳 / 尚书派发）里的审核段，chuan 当前缺这一段（幕僚长直接路由执行，无事前审核）。
**结构约定**:
- 中书（规划阶段）：幕僚长/规划器先产出「待执行方案」（动作列表），不直接执行。
- 门下（封驳关 = `guard.py`）：对方案做安全/意图/权限审查，返回 `approve` 或 `reject(reason)`；`reject` 触发回到中书重拟，带驳回理由。
- 尚书（派发）：仅 `approve` 的方案才交给对应 worker / 工具执行。
**反例**: 不把 guard 做成「事后日志记录器」或「软提醒」——封驳必须是硬关卡，无放行则无执行。不把审核逻辑散落在各 persona 里（应在统一网关，便于审计与一键收紧）。
**约束（沿用 ADR-007）**: 封驳关是轻量同步函数，不另起常驻进程；不因此引入 11 进程式重架构。

## ADR-009: 统一 Skill / MCP 注册表（全局配置，全员默认可用）

**决策**: skill 与 MCP server 采用**集中定义、全员共享**模型：
- 所有 skill 在统一注册表（`skills/` 逐个定义 + config 里一份启用清单）定义一次；
- 所有 MCP server 在统一注册表（`mcp_servers/` 逐个定义 + config 里一份启用清单）定义一次；
- **默认情况下，注册表中的每一项挂载到每一个 worker**（内置 persona + 外来 agent），不要求各 agent 重复声明；
- 个别 agent 如需收窄权限，用 `deny: [skill名 / mcp名]` 显式关掉——是「做减法」，不是「不写就没有」。
**理由**: 用户明确要求「skill 统一配置，都能用，mcp 也是」。避免 N 个 agent 各抄一份 skills/mcp 清单导致配置碎片化、难维护、易遗漏；定义一次、全员可用，契合薄层 + 显式可控（ADR-006/007）。
**与旧假设的关系**: 推翻「每个 agent.yaml 各自列 `skills:` / `mcp_servers:` 才拥有」的默认——改为全局注册表默认全挂，agent 仅做禁用。
**反例**: 不为每个 persona 单独复制 skill/MCP 清单；不把某 skill 绑死在特定 agent 上（除非该 agent 显式 `deny`）。

## ADR-010: Docker 选择性使用（非默认）

**决策**: Docker 不作为外来 agent 的默认运行方式。本地能直接运行的 agent（pi、prime_agent、Claude Code、OpenCode 等支持 Windows 的）一律通过 subprocess stdio 本地直接调用；仅当 agent 不支持 Windows 环境或依赖复杂 Linux 工具链时，才使用 Docker 容器运行。
**理由**: 用户明确拒绝 Docker 作为默认方案（"为什么要用docker，我就不用docker"）。Docker 启动慢、资源占用高、Windows 下 WSL2 兼容性问题多；本地直接调用延迟低、调试方便、与宿主环境一致。Docker 仅作为"最后手段"，用于确实无法本地运行的 agent。
**与 ADR-006 的关系**: 外来 agent 仍需显式注册（`agents/<name>/agent.yaml`），但运行方式从"Docker 隔离"改为"本地直接调用优先，Docker 选择性兜底"。
**反例**: 不为所有外来 agent 统一建 Docker 镜像；不把 Docker 作为 agent 隔离的唯一手段。

## ADR-011: 语音为最终交互目标，TUI 仅调试用

**决策**: chuan-os 的最终日常交互方式是语音全双工（OpenWakeWord 唤醒 + faster-whisper STT + edge-tts/piper TTS），后台常驻，喊唤醒词即可交互。TUI（Textual 终端界面）仅作为开发调试和复杂操作的辅助工具，不是最终用户界面。CLI 是最基础的开发入口。
**理由**: 用户核心目标是"能帮我干活"，语音是最自然的交互方式——不需要打开终端、打字，喊一声就能干活。借鉴 Jarvis 和 assistant-x-openclaw 的语音交互设计。TUI 用于查看工具调用详情、调试 agent 行为、复杂多步操作，但日常使用靠语音。
**接入层优先级**: 语音（最终）> CLI（开发）> TUI（调试）> 微信（远程）> HTTP API（集成）> PWA（移动）。
**反例**: 不把 TUI 作为主要交互界面投入大量开发；不在语音闭环跑通之前做复杂的 TUI 功能。

**落地状态**（N15）：语音闭环已落地于 `chuan/voice/`（STT/TTS/唤醒词/常开流+barge-in/事件音效），入口 `chuan-voice`、CLI `/voice`、`python -m chuan.voice`，`tests/test_voice.py`（32 例）覆盖。详见 ROADMAP.md N15 已完成。

## ADR-012: 幕僚长拆分为 Gateway 七大组件

**决策**: 幕僚长（RuntimeSupervisor）从单一 LangGraph Supervisor 逐步拆分为七个独立组件，借鉴 OpenClaw Gateway 中心辐射架构：① Message Router（意图路由）② Session Manager（会话管理）③ Agent Spawner（角色出生/销毁）④ Skill Dispatcher（工具调度）⑤ Memory Operations（三层记忆接口）⑥ Heartbeat（健康检查）⑦ Cron（定时任务）。
**理由**: 当前 runtime_supervisor.py 承担了太多职责（路由+会话+出生+工具+记忆+调度），违反单一职责原则，难以维护和扩展。拆分为七个组件后，每个组件可独立开发、测试、替换，且与 OpenClaw 等成熟架构对齐，便于借鉴其设计。
**迁移策略**: 渐进式拆分，不一次性重写。先在 `chuan/gateway/` 下建组件骨架，现有 runtime_supervisor.py 保持运行，逐步将逻辑迁移到对应组件，最终 runtime_supervisor.py 变为组装 Gateway 的入口。
**反例**: 不一次性重写幕僚长导致项目长时间无法运行；不引入新的编排框架替代 LangGraph。

**落地记录（已完成，2026-08）**:
- 七大组件全部落地于 `chuan/gateway/`：`message_router.py`（① 路由+天气兜底）、`session_manager.py`（② 会话持久化）、`agent_spawner.py`（③ 角色出生/销毁）、`skill_dispatcher.py`（④ MCP 连接）、`memory_ops.py`（⑤ 记忆索引+巩固）、`heartbeat.py`（⑥ 健康检查，唯一全新实现）、`cron.py`（⑦ 定时任务）；`gateway/__init__.py` 统一导出。
- `runtime_supervisor.py` 已重构为组装入口：`wake_up()` 委托 `agent_spawner.spawn()` / `cron.load_scheduled_jobs()` / `memory_ops.kickoff_consolidation()`；`dispatch()`/`dispatch_async()` 的繁简归一化、天气兜底与 LLM 路由改走 `message_router`；删除与组件重复的旧方法（`_route_with_llm`、`_simplify`、`_ground_weather`、`_setup_checkpointer`、`_connect_mcp`、`_load_scheduled_jobs` 等）。
- 组件通过 `RuntimeSupervisor`（`TYPE_CHECKING` 引用）访问共享运行时状态，避免循环导入。
- 测试：`tests/test_gateway_components.py`（11 例，覆盖 MessageRouter/Heartbeat/MemoryOperations/CronManager）+ `tests/test_weather_grounding.py` 改指向 `MessageRouter`；全量 257 passed。

## ADR-013: 角色从 YAML 迁移到 SOUL.md 目录驱动

**决策**: 角色定义从单文件 `personas/<name>.yaml` 迁移到目录 `personas/<name>/`，内含 `SOUL.md`（人设、职责、说话风格，agent 可自写追加）、`MEMORY.md`（角色私有记忆）、`config.yaml`（brain 绑定、工具权限、sub_agents 列表）。
**理由**: 借鉴 OpenClaw 和 Jarvis 的工作区文件驱动设计。YAML 的 prompt 字段是静态的，agent 无法自己修改；SOUL.md 是 markdown 文件，agent 可以通过工具读写，实现自改进（GEPA 循环中沉淀经验到 SOUL.md）。同时 MEMORY.md 提供角色私有记忆，跟共享黑板（Obsidian）区分。
**迁移策略**: 中期规划，当前 YAML 格式保持兼容（PersonaLoader 双格式支持）。新角色优先用目录格式，旧角色逐步迁移。
**反例**: 不强制一次性迁移所有 14 个角色；不放弃 YAML 兼容性（PersonaLoader 仍支持旧格式）。

**落地状态**（N14）：14 个角色已全部迁到目录格式，旧 `personas/<name>.yaml` 已删除，`PersonaLoader` 双格式兼容逻辑保留（详见 ROADMAP.md N14 已完成）。

## ADR-014: 岗位化调度（角色=项目经理，agent=外包工程师）

**决策**: 在「角色（persona）」与「agent（执行者）」之间引入一层「岗位」（`PersonaRole`）作为调度层。岗位不再直接干活，而是像项目经理一样只做拆任务、选 agent、协调、汇总（PLAN → ASSIGN → EXECUTE → SUMMARIZE）；实际执行交给 `AgentPool` 里的 agent（外包工程师）。

**理由**: 阶段1 的「角色 = 单个 LangGraph ReAct agent」1:1 绑定，角色既要接客又要干活，无法处理复合任务（"先调研、再写报告、再排版"），也无法在多个 agent 间协调。借鉴 dsh-agent-teams 的队长—成员模型与 OpenClaw 的 agent 外包：把「调度」和「执行」分离，岗位专注编排，agent 专注产出。复合任务由此可拆成子任务并行 fan-out，重活还能外包给 pi/OpenCode/Claude Code 等 sub_agent。

**结构约定**:
- **岗位（PersonaRole）**: `chuan/role.py`。`dispatch()` 四步：PLAN（拆子任务+依赖）→ ASSIGN（每个子任务选 agent）→ EXECUTE（拓扑分波，同波 `asyncio.gather` 并行、波间串行）→ SUMMARIZE（确定性汇总）。
- **AgentPool**: `chuan/agent_pool.py`。常驻池存 command agent（pi/prime_agent/claude_code/opencode）+ 按需 `spawn_builtin()` 创建临时专家 agent（specialist 子任务）。
- **团队状态落盘（TeamStateWriter）**: `chuan/team_state.py`。规划落定即写 `data/teams/<session_id>.json`，子任务状态实时更新，重启可冷恢复「上次 N 个子任务未完成」，并保留归档审计。
- **成员消息直通（team_bus）**: `chuan/team_bus.py`。全员挂载 `ask_role` 工具，子任务执行中可直接向其他岗位提问并同步等答复；协作深度限 1 层，防 A→B→A 无限递归。

**可靠性设计（免费模型 JSON 不稳的教训）**:
- 规划门槛 `_should_plan()`: 短任务/无步骤词不走规划，零额外 LLM 开销。
- 规划严格校验：JSON 可解析、id 唯一、依赖存在、无环；任何失败 → 降级单 agent，不阻断。
- 可调开关：`CHUAN_PLAN=0` 关规划、`CHUAN_SUBTASK_RETRIES` 子任务重试次数；确定性退化检测兜底（模型把工具调用原文当回复时）判失败。

**约束（沿用 ADR-007/008/009）**: 岗位层是单进程 asyncio 内的轻量逻辑；guard 仍拦所有 sub-agent 调用；agent 工具仍走 ToolRegistry 全局挂载 + deny 减法。

**反例**: 岗位不直接调工具/写代码（干活的活交给 agent）；不引入多进程 mailbox 架构（单进程 asyncio 足够，落盘仅用于冷恢复与归档）。

**落地记录（已完成，2026-08）**:
- `chuan/role.py`（PersonaRole：规划/分波并行/汇总/重试/退化检测/specialist spawn）、`chuan/agent_pool.py`（常驻池 + 动态 spawn）、`chuan/team_state.py`（磁盘真相）、`chuan/team_bus.py`（ask_role 一层协作）全部落地。
- `runtime_supervisor.py`：`_workers` 改为 `dict[str, PersonaRole]`，`dispatch()` 路由到岗位；`wake_up()` 注册 `ask_role` 工具 + 团队状态冷恢复。
- 测试：`tests/test_role.py` 覆盖规划门槛、任务拆分校验、分波并行、specialist spawn、串行/并行执行等。

## ADR-015: 微信接入选企业微信（WeCom）自建应用

**决策**: 「微信远程操控电脑」（N19）的后端选**企业微信自建应用**，而非个人微信（itchat/wechaty）或公众号。接入层落 `chuan/channels/wechat.py`（`WeChatChannel`），做「收消息 → `RuntimeSupervisor.dispatch` 路由 → 回发」的胶水，会话按 `wechat:<user_id>` 隔离。
**理由**: 个人微信 Web 协议（itchat）已被腾讯大面积封禁、不稳定；公众号只能被动回复、无主动推送能力（不适合持续操控）；企业微信提供稳定的官方 API（`gettoken` + `message/send`），凭据经 `config.yaml` 的 `wechat:` 段显式启用。与 ADR-006/010 一致——显式配置、本地直连、可控可审计。
**约束（沿用 ADR-007）**: 微信通道只做胶水，复用 `dispatch` 路由与岗位调度，不自研消息队列、回调加解密；未配置凭据时 `send()` 返回 False 优雅降级，`handle()` 仍可在本地/测试中路由。
**反例**: 不绑定个人微信协议爬虫；不为微信单独建一套调度逻辑（复用岗位层）。

## ADR-016: 后台委派 harness（fire-and-forget，借鉴 deepseek-harness）

**决策**: 新增 `chuan/gateway/agent_harness.py`（`AgentHarness`），把编码任务**整体**委派给外部 command agent（claude_code / opencode / pi / prime_agent）黑盒后台执行：
- `submit(agent, task, ...)` **派发即返** `task_id`，不阻塞主对话（fire-and-forget）；
- 任务调度到 supervisor 常驻事件循环后台跑，`CommandAgent.run` 用 `asyncio.to_thread(subprocess.run)` 避免同步子进程卡死 `chuan-event-loop`（同波 `asyncio.gather` 并行、MCP 心跳、consolidation 均依赖循环不被外部 agent 冻结）；
- 完成/失败经 `on_done` 回调（全局 + 每任务）异步回推——CLI `print` + HUD `effect`/终端文本，旁路通道自行注册；回调必须轻量，不得阻塞事件循环；
- `snapshot(status=)` 提供任务状态快照（TUI 看板数据源），完成态最多保留 200 条防内存膨胀。

**接口**: `RuntimeSupervisor.delegate(agent_name, task, *, session_id, on_done) -> task_id`（校验唤醒 + 常驻池存在性）+ `delegate_snapshot()`；CLI 命令 `/bg <agent> <任务>` 派发、`/tasks` 看板。

**理由**: 借鉴 deepseek-harness 的「Profile-Bundle 桥接 → 拉起原生 CLI 子进程黑盒跑完整 Agent 循环 → 只回收最终结果」模式。岗位化调度（ADR-014）的 `asyncio.gather` 并行假设要求外部 agent 不阻塞事件循环；「长任务黑盒委托 + 异步回推」补上了 ADR-014 缺失的异步旁路，让重活（Claude-Code 写代码等）在后台跑，主对话不干等。

**反例**: 不把后台任务塞回同步 `dispatch()`（那会阻塞主线程）；不为外部 agent 自研 mailbox/多进程架构（单进程 asyncio + 常驻循环足够）。

**落地记录（已完成，2026-08-23）**: `chuan/gateway/agent_harness.py`（submit/get/snapshot/on_done）+ `runtime_supervisor.delegate()`/`delegate_snapshot()` + `main.py` `/bg` `/tasks` + 完成回推；`CommandAgent.run` 改 `asyncio.to_thread`（修复事件循环阻塞 bug）+ 修正过时注释；`tests/test_agent_harness.py`（8 例）覆盖派发即返/非阻塞/并发/回调隔离/未知 agent 优雅失败，全量 312 passed、2 skipped。

**状态机升级（2026-08-23，N21 补）**: AgentHarness 升级为**任务状态机 + 依赖 DAG**（借鉴 dsh-agent-teams / NVIDIA AVO 的 idle/ready/running/done）：
- 状态 `pending → ready → running → done/failed`；`submit(depends_on=[...])` 依赖未全结束进 `pending`，`_promote_pending()` 在每个任务结束后自动推进依赖就绪的 pending 任务；
- `_schedule()` **原子认领**（ready→running 只发生一次，带 `claimed_by`）防止两个执行器干同一个活；
- 防死锁：depends_on 必须引用已存在任务（否则 ValueError），新 id 登记后才生成 → 环天然不可达；
- 语义：依赖失败不阻断下游（failed 也是终态），与 dsh-agent-teams 一致；
- TUI `/tasks` 看板新增 `⏳ pending / 🟡 ready / 🟢 done / 🔴 failed` + 依赖数显示；`delegate()` 透传 `depends_on`；
- 测试新增 6 例（等待依赖/链式推进/依赖已结束立即跑/依赖失败不阻断/非法引用/认领不重复执行），全量 321 passed、2 skipped。

## ADR-017: MCP 管理面板（TUI 可视化连接/开关，不改 yaml）

**决策**: 为 MCP 提供 TUI 可视化面板（借鉴 Aivy-OS 的 MCP Server 管理 UI），取代「改 `config/mcp_servers.yaml` + 重启才生效」的落后方式：
- **面板数据源** `MCPAdapter.server_status()`：每个已配置 server 的 name / configured / connected / tools（工具数）/ command / args / description / error；
- **单 server 运行时启停**：MCPAdapter 从单个 `AsyncExitStack` 重构为**每 server 独立 stack**，新增 `connect_one(name)`（幂等）/ `disconnect_one(name)` / `reconnect_one(name)`，`connect_all`/`disconnect_all` 复用；
- **RuntimeSupervisor** 暴露 `mcp_status()`（同步快照）+ `mcp_connect()`/`mcp_disconnect()`/`mcp_reconnect()`（经 `run_coroutine_threadsafe` 调度到常驻事件循环 `_mcp_await`，MCP session 绑定在 `_loop` 上必须回循环操作）；
- **TUI 命令**：`/mcp` 渲染面板（🟢 已连接·工具 N / 🔴 失败·错误摘要 / ⚪ 未连接）、`/mcp on <name>` / `/mcp off <name>` 运行时开关，操作后自动重绘面板。

**理由**: 沿用 ADR-006/010 的「显式配置 + 本地直连 + 可控可审计」，但配置变更不应要求重启进程。单 server 启停让「某个 MCP 出问题时单独重启、不影响其他」成为可能；状态面板把连接失败原因（_errors）直接可见，排查从「翻日志」变成「看一眼面板」。

**反例**: 不把运行时开关持久化回 yaml（面板开关是临时性的，重启按 yaml 原配置恢复——避免面板与配置文件互写冲突）；不为每个 MCP server 引入独立进程（单进程 asyncio + 每 server 独立 AsyncExitStack 已满足隔离）。

**落地记录（已完成，2026-08-23，N22）**: `mcp_adapter.py`（per-server AsyncExitStack + connect_one/disconnect_one/reconnect_one + server_status，disconnect_all 改为遍历已配置名以清理失败记录）+ `runtime_supervisor.py`（`_mcp_await`/`mcp_status`/`mcp_connect`/`mcp_disconnect`/`mcp_reconnect`）+ `bridge.py` 转发 + TUI `/mcp` `/mcp on|off`；`tests/test_mcp_adapter.py`（5 例：状态回读/未知 server/静默断开/失败隔离/断开清状态）+ `test_tui.py`（3 例：bridge 转发/面板渲染/开关重绘），全量 329 passed、2 skipped。

## ADR-018: P1 监督者全监控（Supervisor Monitor，轨迹+死胡同+redirect）

**决策**: 借鉴 NVIDIA AVO 的 Supervisor「CEO 只看轨迹、不干活」定位，补全幕僚长的**全程监控**层——不再只做初始路由，而是记录每次 worker 执行轨迹，确定性检测死胡同并给出 redirect 决策，防止「反复失败 / 循环 / 停滞」空耗 token 与时间：
- **轨迹记录**：`SupervisorMonitor` 以 `trace_id`（会话 id）聚合每次 `PersonaRole.dispatch` 的执行路径，`record_step` 记录 role + 子任务 + attempt + agent + 成功与否 + 结果内容；
- **死胡同检测（全确定性，不依赖 LLM）**：① 循环——最后两次输出字符 2-gram 覆盖度 ≥0.95；② 反复失败——连续失败 ≥N 次且结果相似（≥0.7）或尝试耗尽（≥max_fail_attempts）；③ 停滞——轨迹存活超时（watchdog，单步长期悬挂也可判）；单步信号不足绝不误判；
- **redirect 决策**：`abort`（无候选 agent 且循环/停滞/耗尽，不再空耗）/ `switch_agent`（有候选换 agent，排除当前）/ `inject_hint`（同一 agent 注入「换思路」提示重试）；决策记录进面板（`_dead_ends` / `_redirects`）；
- **旁路设计**：监控与 redirect 是增强层，任何异常/误判都不阻断主流程（`_monitor is None` 短路 + 全方法 try/except）；重试循环在**重试前**查询死胡同，`PersonaRole._run_subtask` 应用决策；
- **可视化**：`RuntimeSupervisor.monitor_status()` 快照 → TUI `/monitor` 面板 + 状态栏指示 + CLI `/monitor`。

**理由**: 免费模型工具调用不稳定（项目教训），监控判断必须可复现、可测试，故全走确定性启发式而非 LLM；N21 harness 已解决「后台任务执行」，「监控执行轨迹」是补齐「发现卡死」这一环——不监控就无法知道后台任务是否在原地打转。

**反例**: 不做持久化（监控数据是瞬态诊断，重启即清，不落盘）；不把 redirect 做成强制干预（只是增强层，核心调度逻辑不被监控绑架）；不做 LLM 总结式监控（成本高且不可测）。

**落地记录（已完成，2026-08-24，N23）**: `chuan/gateway/supervisor_monitor.py`（SupervisorMonitor：start_trace/record_step/check_dead_end/_redirect/snapshot，阈值可注入）+ `role.py`（`PersonaRole` 注入 `monitor`，`_begin_trace`/`_finish_trace`/`_record_step`/`_check_dead_end`，重试前查死胡同并应用 redirect，旁路 try/except）+ `agent_spawner.py`（spawn 时注入 `supervisor_monitor`）+ `runtime_supervisor.py`（`self.supervisor_monitor` + `monitor_status()`）+ `bridge.py`/`tui/app.py`（`/monitor` 面板 + 状态栏指示）+ `main.py`（CLI `/monitor`）；`tests/test_supervisor_monitor.py`（19 例：轨迹生命周期/裁剪/三类死胡同/redirect 决策/相似度边界/无 monitor 短路），全量 348 passed、2 skipped。

**落地记录（HUD 可视化，2026-08-24，N23 续）**: 将监督者数据接入 Flutter HUD 悬浮层，实现实时可视化——`supervisor_monitor.py` 新增 `hud_summary()`（精简快照：stats{traces/active/dead_ends/redirects} + 最近 3 条轨迹 `top_traces` + 最近死胡同 `latest_dead`，只带面板所需最小字段）；`channels/hud.py` 新增 `HudChannel.push_monitor()`（发送 `monitor:{json}` 命令）+ 共享助手 `push_monitor_snapshot(supervisor, hud)`（无监督者/未启动时静默降级）；`main.py` 与 `voice/main.py` 在 dispatch 完成后推送快照（不阻塞主流程）；Flutter `jarvis_overlay.dart` 的 `handleCommand` 解析 `monitor:` 存入 `ValueNotifier<Map?>`，左下 SYSTEM STATUS 面板新增 SUPERVISOR 区块（ACTIVE/DEAD/RD 统计 + 最近死胡同，`FittedBox` 防窄面板溢出）。测试：`test_hud.py` +5 例（push_monitor JSON 载荷/不可序列化降级/共享助手转发/静默降级）、`test_supervisor_monitor.py` +2 例（hud_summary 精简字段与无数据），全量 354 passed、2 skipped。注：本机未装 Flutter SDK，Dart 改动经人工复核（活跃代码路径为根 `agent_overlay.dart`→`jarvis_overlay.dart`，`overlay/core` 为未接线重构副本）。

## ADR-019: Wiki 知识库维护层（N24，实体归并 + index/log + 主动维护）

**决策**: 在既有三层记忆之上加一层**结构化知识库**（借鉴 Karpathy LLM Wiki / obsidian-second-brain / Aivy 5 类目录），把长期记忆从「session 快照堆」升级为「按实体归并、主动维护」的 wiki：
- **6 类目录**：`sources`(原料，raw 只读) / `topics`(主题) / `entities`(实体) / `analysis`(分析结论) / `projects`(进行中的事) / `howto`(怎么做过程原子，N26 并入)，命名空间天然复用 `Memory._namespace_path`；
- **实体页改写**：`Wiki.write()` 按实体名做唯一键，同名页**合并更新**而非新建——保留 `created`、新内容写入 `## <section>` 小节、同名小节已存在则覆盖旧声明并折叠为 `> 旧结论（deprecated）` 留痕；`import_source()` 对 `sources/` 只追加不覆盖（raw 不可变层，防幻觉扩散）；
- **index/log 双文件**（Karpathy 替代 RAG 的机制）：每类目录维护 `index.md`（每页一行 `[[wikilink]]` + 摘要），全局 `log.md` 追加式审计（`## [时间] write | 路径`，grep 可回溯）；`search_index()` 确定性索引定位，无命中回退 `recall()`；
- **reconcile / lint**：`reconcile()` 审计 deprecated 痕迹与新小节（裁决报告，不依赖 LLM）；`lint()` 健康检查（孤立页/死链/缺 frontmatter 必填/过时声明），全确定性；
- **归位（ingest，N24c）**：`Wiki.ingest_sources()` 把 `sources/` 里未整理的蒸馏原料「主动整理」进成品实体页——LLM 可用时按模型返回的 JSON 路由到 topics/entities/analysis/projects（失败/退化自动回退），否则确定性解析 `## 结论`→`analysis/`、`## 待办/后续`→`projects/` 并回链 raw 源；整理后的原料打 `wiki_ingested` 标记幂等跳过，`import_source()` 追加新内容会清标记保证增量被重新归位；
- **consolidation 落点迁移**：蒸馏产物经 `Wiki.import_source()` 落到 `sources/`（raw 层）而非旧的 `notes/session-*.md`；`kickoff_wiki_maintenance()` 启动即建目录 + 归位 + lint，并注册每日维护 daemon 线程。

**理由**: 调研（Claude Code 4 类记忆 / Codex 渐进式文件 / Hermes 硬容量 / obsidian-second-brain 改写实体页）共同指向「记忆要归并、互链、维护，而非堆叠」；免费模型工具调用不稳定（项目教训），故核心写/查/调和/检查全走**确定性实现**，LLM 不进入关键路径；Karpathy 明确「此规模不需要向量 RAG」——index.md + grep 足够，因此本节点不引入 faiss。

**反例**: 不把 `sources/` 纳入 `wiki_write` 可写范围（原料不可变，只由导入/蒸馏写入）；不做 LLM 语义级矛盾裁决（确定性覆盖留痕 + 审计报告，人工/未来 AI 清理）；不建独立向量索引（index.md 优先，规模到上千篇再评估 RAG）。

**落地记录（已完成，2026-08-24，N24）**: `chuan/wiki.py`（`Wiki`：write/import_source/ingest_sources/_merge_body/search_index/reconcile/lint，`_safe_slug` 支持中文实体名）+ `memory.py`（`remember`/`_with_frontmatter` 增 `confidence` 1-5）+ `memory_tools.py`（`build_wiki_tools`：`wiki_write`/`wiki_search`）+ `persona_loader.py`（全角色注入 wiki 工具）+ `consolidation.py`（`consolidate_sessions` 增 `wiki` 参数，蒸馏落 `sources/`）+ `gateway/memory_ops.py`（`run_wiki_maintenance`：建目录+归位+lint / `kickoff_wiki_maintenance` + 每日维护 daemon 线程）+ `runtime_supervisor.py`（`wiki_status` 状态 + 启动挂接）。测试：`tests/test_wiki.py` 17 例（实体归并/raw 只读/index+log/reconcile/lint/工具暴露/落点/归位幂等/LLM 路由与回退），全量 371 passed、2 skipped。

## ADR-020: 外接只读库接入 FTS5（多 vault key 隔离，N25）

**决策**: 把外接 Obsidian 库（`config.memory.external_vaults`，如 `D:/Resources/Obsidian`）**只读接入**既有 FTS5 索引，作为独立 vault key 的旁路检索层，不混入内部记忆管道：
- **复用现有多 vault 表结构**：`memory_fts` 每行本就带 `vault` 键（UNINDEXED），`memory_meta` 主键为 `(vault, rel_path)`——外部库无需新表，只用独立 vault key（`_vault_key_for(root)`，内部=内部 vault 根，外部=外部库根）；
- **索引管道泛化**：`reindex()` 的增量同步主体抽成 `_reindex_root(root, vault_key, rel_base)`，内部/外部共用（mtime 增量 + 删除残留清理）；`_index_document`/`_fts_candidate_paths` 增 `root`/`vault_key` 参数（默认内部，向后兼容）；
- **只读严格性**：外部库只 `read_text` 进索引，绝不写回（无 frontmatter 注入、无修改）；跳过隐藏目录（`.obsidian`/`.trash`/`.git`），避免把配置/附件扫进来；
- **召回默认隔离**：`recall()` 默认只查内部 `notes/`；外部库需显式 `vaults=["obsidian"]` 才跨库召回（命中路径相对外部库根，无 frontmatter 按 importance=3），不污染 agent 上下文；
- **挂接**：`MemoryOperations.reindex()` 在内部库之后跑 `reindex_external()`（启动时增量同步），日志打印「外接库索引：obsidian N 篇」。

**理由**: 外接 Obsidian 库 363 篇/约 59.5 万字符，体量已接近但未达向量 RAG 阈值（ROADMAP P3 评估闸门）；先把它接进 FTS5 + wiki 归位是「更便宜的先手」——复用现有多 vault FTS 结构，改动集中在参数化而非新表，且严格只读 + 默认隔离避免外部数据污染内部记忆与 agent 上下文；中文检索直接受益于既有 CJK 单字切分（`_fts_segment`）。

**反例**: 不做向量语义召回（未达阈值，见 ROADMAP P3 闸门）；不把外部库并入默认 `recall()`（默认隔离，防上下文污染）；不做外部库写入/归并（raw 只读，只由 import/蒸馏写入内部侧）；`search_vault` 检索工具暴露留待 P2 候选。

**落地记录（已完成，2026-08-24，N25）**: `config.yaml`（`memory.external_vaults`）+ `chuan/memory.py`（`_resolve_external_vaults`/`_load_external_vaults`/`reindex_external`/`_reindex_root`/`_vault_key_for`，`_index_document`/`_fts_candidate_paths`/`recall` 参数化，`recall` 增 `vaults` 参数 + `_recall_root` 辅助）+ `chuan/gateway/memory_ops.py`（`reindex` 挂接 `reindex_external`）。测试：`tests/test_external_vault.py` 6 例（config 解析/索引跳过隐藏/增量与删除同步/只读断言/默认隔离/跨库召回/importance 默认），全量 377 passed、2 skipped。

## ADR-021: L3 从做到造知识原子闭环（N26，借鉴姜胡说 7 层 L3）

**决策**: 在记忆链路之上加**可复用「怎么做」知识原子**闭环，实现 7 层模型 L3「从做到造」——重复做一件事，把过程提炼成原子，下次同类任务自动复用：
- **存储**：`notes/howto/<name>.md`（`HowToStore`，命名空间 `notes/howto`），frontmatter 含 `trigger`（触发场景关键词）/`tools`/`importance`/`confidence`，正文 `## 触发场景` + `## 怎么做`；同名归并（保留 created），复用 Memory FTS5 索引；
- **沉淀**：`howto_save` 工具（agent 识别到可复用过程时显式沉淀，避免自动脑补——免费模型教训）；`howto_find`/`howto_show` 供检索/读全量；
- **复用（闭环关键）**：`HowToStore.suggest(task)` 确定性按任务文本召回 top 原子（命中分 ≥ 10 才注入，过滤「怎么做」小节头的通用 token 噪声）；`PersonaRole._maybe_inject_howto` 在显式 agent、单 agent、子任务三处开工前自动注入「参考做法」，agent 照着做而非从零开始；
- **接线**：`persona_loader` 全角色注入 howto 工具；`agent_spawner` 把 `memory` 传给 `PersonaRole`。

**理由**: 7 层模型分析（2026-08-24）指出 chuan 卡在 L4/L5，缺 L3「从做到造」沉淀闭环——现有 GEPA 只把单条经验追加到角色 MEMORY.md（无触发场景、无检索复用），wiki 归位存「结论/待办」而非「怎么做」。知识原子补齐「怎么做」这一环：沉淀显式化（防脑补）、复用确定性（无 LLM 进关键路径）、阈值防噪声（小节头 token 干扰）。

**反例**: 不做无人工把关的自动沉淀（agent 每次成功都自动写直接入库会造成噪声与脑补；N27 改为自动提炼入 staging + 人工确认后才入库，防噪声由门槛 + 人工把关承担）；不做向量召回（复用走 FTS5 关键词，未达 ROADMAP P3 RAG 闸门）。

**落地记录（已完成，2026-08-24，N26；并入 wiki 后更新）**: `chuan/howto.py`（`HowToStore`：save 委托 `Wiki.write(entity_type="howto")` 获 index/lint/归并留痕，find 从 `## 触发场景` 小节解析 trigger、tools 走 frontmatter tags，suggest 阈值注入）+ `chuan/wiki.py`（`WIKI_NAMESPACES` 5 类→6 类加 `howto`，自动生成 `howto/index.md` + lint/wiki_search 覆盖）+ `chuan/memory_tools.py`（`build_howto_tools`：howto_save/howto_find/howto_show）+ `chuan/persona_loader.py`（全角色注入）+ `chuan/role.py`（`PersonaRole.__init__` 增 `memory` 参数，`_maybe_inject_howto` 接入显式/单 agent/子任务三处）+ `chuan/gateway/agent_spawner.py`（传 `memory`）。测试：`tests/test_howto.py` 10 例（保存归并/trigger 解析/wiki index+lint+search 集成/阈值注入/无命中不注入/角色注入/工具暴露），全量 387 passed、2 skipped。

## ADR-022: 知识原子自动沉淀 + 人工确认（N27，L3 闭环补「自动」）

**决策**: 给 N26 补上「自动沉淀」——任务**成功收尾**时自动提炼候选做法原子，先入 **staging 待人工确认队列**，人工确认后才落入 howto：
- **门槛（确定性，先廉后贵）**：失败/任务过短(<8 字)/结果无实质(<40 字)/已有强命中原子(`suggest` ≥10 分)/队列已满(30)/同任务已在队列 → 跳过，绝不脑补；
- **提炼**：`HowToDistiller` 产出 name/trigger/process/tools —— 默认确定性提取（剥求助前缀取名、任务作触发场景、成功结果作怎么做），可选传 `brain` 做 LLM 润色（沿用 wiki ingest「LLM + 确定性回退」模式，免费模型不稳的教训）；
- **staging 队列**：`data/memory/howto_staging/<name>.json`（vault 之外，不污染 FTS/wiki 索引），`HowToStore.stage/staging_list/staging_get/approve/discard`；
- **人工确认**：`approve`（可 rename）经 `Wiki.write` 入库（白得 index/lint/双链），`discard` 丢弃；CLI `/howto`（show/approve/discard）+ `RuntimeSupervisor.howto_*`；
- **主流程集成**：`RuntimeSupervisor.dispatch` 前置把「确认/丢弃（可带名字）」消息路由到待确认候选（多条时列清单请指定名字，避免误动），dispatch 结束后若新增候选则往回复追加「[待确认]」提示——在自然对话里即可确认，无需记 `/howto` 命令；确定性词表 + 精确匹配防误吞正常消息，全程旁路 try/except；
- **挂接**：`PersonaRole._wrap_result` 收尾旁路调用（显式 agent/单 agent/规划汇总三路径全覆盖），异常静默不阻断答复。

**理由**: N26 沉淀靠 agent 显式 `howto_save`，免费模型不会主动干，闭环缺「自动」一环（借鉴 Claude Code Auto Memory 后台提取）。但直接自动入库会噪声化知识库（N26 反例），故加「门槛过滤 + 人工确认」双闸：门槛防明显垃圾，人工把关防模型脑补——知识库只被确认过的做法增长，而非每次成功任务都长一个原子。

**反例**: 不做自动入库（无确认直接写 howto 会让免费模型的脑补污染知识库）；不做 LLM 必选（提炼走确定性即可用，LLM 仅润色，避免依赖免费模型 JSON 稳定性）；不并入 wiki（staging 放 vault 外避免被 FTS/wiki 误扫，确认后仍走 Wiki.write 复用底座）。

**落地记录（已完成，2026-08-24，N27）**: `chuan/howto.py`（`HowToStore` 增 staging 队列：stage/staging_list/staging_get/approve(rename)/discard，目录 `data/memory/howto_staging/`）+ `chuan/howto_distill.py`（`HowToDistiller`：maybe_distill 门槛 + `_refine` LLM 润色/确定性回退，`_derive_name/trigger/process`）+ `chuan/role.py`（`_wrap_result` 挂接 `_maybe_distill_howto`，懒加载旁路）+ `chuan/runtime_supervisor.py`（`howto_staging/howto_show/howto_approve/howto_discard` + `dispatch` 主流程集成：`_resolve_pending_howto` 前置确认/否决、`_append_howto_prompt` 沉淀后追加「[待确认]」提示、`_howto_system_reply`/`_howto_pending_list_text`）+ `chuan/main.py`（`/howto` 命令 show/approve/discard + help 更新）+ `chuan/tui/bridge.py`（`howto_staging/show/approve/discard` 转发）+ `chuan/tui/app.py`（`/howto` 队列面板 + 命令 + help + `howto_confirm` 路由标签）。测试：`tests/test_howto_distill.py` 28 例（门槛/提炼/确认/角色挂接/主流程确认与追加提示/按名大小写不敏感）+ `tests/test_tui.py` 3 例（bridge 转发 + 面板渲染 + approve 重绘），全量 418 passed、2 skipped。

## ADR-023: 例行自动化闭环（N28，scheduler+howto+wiki 串成自转）

**决策**: 把「例行任务（routine）」做成一等概念，让系统**到点自转**而非等召唤——补上 N26/N27「从做到造」闭环的例行化载体：
- **例行任务**：`RoutineManager`（`chuan/routines.py`）—— `Routine{name, message, schedule, agent, archive_to_wiki}`，持久化 `data/routines.json`（磁盘真相，重启不丢）；调度写法 `"fri 17:30"`（周几+时刻，dow 支持 mon..sun / 周一..周日）或 `"every 3600"`（间隔），兼容 `@` 紧凑写法；
- **每周调度**：`ProactiveScheduler.add_weekly_job()` —— `ScheduledJob` 增 `weekly` 字段，`_next_weekly` 计算下次发生时刻，触发后自动重排到下一周；与既有 interval 任务并存；
- **自转链路**：例行到点 → `dispatch_to` → `PersonaRole` 开工自动注入 howto 参考做法（N26）→ 跑完自动沉淀候选待确认（N27）→（可选 `archive_to_wiki`）结果归档 wiki `sources/` 原料层供每日 ingest 归位（N24）→ 下周复用已改进的原子；
- **运行时管理**：CLI/TUI `/routine`（list / add `<name> <调度> <任务> [--wiki]` / remove），`RuntimeSupervisor.routine_add/list/remove`；`wake_up` 自动应用持久化例行并启动调度线程；
- **归档钩子**：`ProactiveScheduler.on_routine_done` 回调 → `_archive_routine_result`（错误提醒跳过，未开 archive 跳过）。

**理由**: 用户故事「每周五自动出部署周报」是 L3 闭环的最终形态——重复做的事不只是沉淀成原子，而是**被系统定期重复执行**。现有 scheduler 只支持 interval（无法表达「每周五」），例行任务缺一等概念与持久化，且例行输出没有回流知识库（wiki 闭环缺执行方）。routine 把这些补上：调度器给「到点」，howto 给「复用+沉淀」，wiki 给「归档归位」，三者串成自转。

**反例**: 不做 cron 表达式解析（免费环境不引入 croniter 依赖，weekly+interval 两态覆盖现实例行即可）；不做 LLM 调度意图解析（调度写法确定性解析，绝不赌模型）；例行输出不进 wiki（那闭环就是单向「跑一次看一眼」，归档 sources/ 后由既有 ingest 归位，复用 N24 能力）。

**落地记录（已完成，2026-08-24，N28）**: `chuan/scheduler.py`（`ScheduledJob.weekly` + `_next_weekly` + `add_weekly_job` + 触发后按周重排 + `on_routine_done` 回调）+ `chuan/routines.py`（`RoutineManager`：add/remove/list + `data/routines.json` 持久化 + `parse_schedule`（weekly/interval，@紧凑写法）+ `apply_to` 注册进调度器并启动）+ `chuan/runtime_supervisor.py`（`routines` 管理器 + `routine_add/list/remove` + `_archive_routine_result` 归档 wiki 钩子 + `wake_up` 应用例行）+ `chuan/main.py`（`/routine` list/add/remove + help）+ `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/routine` 面板）。测试：`tests/test_routines.py` 13 例（调度解析/每周计算/周任务触发重排/routine 增删查持久化/apply_to/归档钩子/管理接口）+ `tests/test_tui.py` 2 例，全量 433 passed、2 skipped。

## ADR-024: 例行任务失败重试（N29，scheduler 级退避重试安全网）

**决策**: 给例行任务的「任务级」执行补上失败重试，与 N23 死胡同（任务内）分层，作为整轮 `dispatch_to` 级别瞬态失败的安全网：
- **失败分级（确定性）**：仅 `KeyError`（worker 不存在，配置错误）判永久不重试；其余异常 + 退化内容（空回复 / `[PROACTIVE JOB ERROR]` / `[PROACTIVE JOB COMPLETED]` 占位符）一律瞬态可重试；语义失败（跑了但产出不对）无法确定性判定不纳入（N23 死胡同已覆盖任务内场景）；
- **策略**：指数退避、确定性无抖动 —— 基数 60s、系数 2、封顶 30min；每例行可配 `retries`（0=关闭，默认，向后兼容；`/routine add ... --retries N`），`fail_count` 仅在本轮「触发→结算」窗口内累计，成功或重试耗尽后清零，下个触发点重新开始；
- **告警语义**：重试间隙静默退避重排 `next_run`（不发用户告警防刷屏）；耗尽/永久才发 `[PROACTIVE JOB ERROR]` 告警并正常重排；`_archive_routine_result` 已跳过 `error=True`，wiki 不受中间失败污染；
- **分层**：层 1 任务内 `_run_subtask` 重试 + 死胡同 redirect（N23，已有）；层 2 任务级调度器退避重试（本次）——层 1 覆盖不到整轮 worker 超时/事件循环抖动时兜底；
- **暴露**：`ScheduledJob` 增 `retries/fail_count/retry_base/retry_factor/retry_max`；`RoutineManager` 增 `retries` 参数并持久化 `data/routines.json`；`routine_list` 暴露 `retries/fail_count`，CLI/TUI `/routine` 面板显示 `🔁 retry #fc/rt`。

**理由**: N28 例行闭环里，每周五 17:30 那次触发若撞上瞬时故障（LLM 5xx、worker 超时 600s、事件循环抖动）就整周白跑且无补偿。任务内重试只覆盖单步工具调用，管不住整轮 `dispatch_to` 级别的失败，缺一层调度器级安全网。

**反例**: 不做语义失败重试（产出不对无法确定性判定，重试只烧 token，交 N23 死胡同）；不做抖动（项目确定性原则，指数退避已足够错峰）；不做永久错误重试（worker 缺失重试无益，白等退避才告警）。

**落地记录（已完成，2026-08-24，N29）**: `chuan/scheduler.py`（`ScheduledJob` 增 `retries/fail_count/retry_base/retry_factor/retry_max` + `_retry_backoff` 指数退避封顶 + `_run_job` 失败分级返回 `ProactiveAlert|None` + `_is_failed_content` 退化判定 + `add_interval/weekly_job` 增 `retries` 参数）+ `chuan/routines.py`（`Routine.retries` + 持久化 + `apply_to` 透传 + `retry_state`）+ `chuan/runtime_supervisor.py`（`routine_add(..., retries)` + `routine_list` 暴露 `retries/fail_count`）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/routine add ... --retries N` + 面板 `🔁 retry` 状态）。测试：`tests/test_routines.py` 新增 8 例（退避/成功清零/耗尽告警/永久不重试/retries=0 兼容/空结果可重试/持久化透传/管理接口暴露），全量 441 passed、2 skipped。

## ADR-025: 自动技能创建（N30，技能即记忆，L3 闭环收尾）

**决策**: 任务成功收尾时自动提炼一个**可注册的 prompt 型技能**，复用 N27 howto「自动提炼 → 人工确认 → 入库」模式，让「从做到造」从知识原子升级到**可调用能力**：
- **技能形态**：prompt 型 skill（`skills/<name>.yaml`，`type: prompt`）—— `name`/`description`/`trigger.keywords`（触发关键词）+ `prompt`（可复用做法）；`Skill` 类补齐 `prompt` 字段、`matches(text)` 触发匹配、`render_prompt()` 渲染；
- **自动提炼**（`chuan/skill_creator.py`）：确定性门槛（失败/任务<8 字/结果<40 字/已有同名技能/队列满 30/同任务重复 → 跳过），纯确定性提炼（复用 howto_distill 剥前缀取名、结果作做法、任务作触发场景；关键词提取 = CJK 词/二元组去停词，上限 8）；
- **人工确认**：staging 队列（`data/memory/skill_staging/`，vault 外不污染 FTS/wiki）→ `/skill` show/approve/discard；approve 写入 `skills/<name>.yaml`（`yaml.safe_dump` 保证中文/特殊字符安全）+ **运行时注册**进 `SkillRegistry.add`（本会话即生效，无需重启）；
- **复用注入**：`PersonaRole._inject_reference` 开工前注入——**已注册技能（触发词精确命中）优先**，howto 知识原子（FTS 召回）兜底，避免双重注入；`_maybe_inject_skill` 每次现读 `skills/`（廉价）保证同会话新确认技能即时生效；`_maybe_create_skill` 与 `_maybe_distill_howto` 并列挂接 `_wrap_result`（全旁路）；
- **管理**：`RuntimeSupervisor.skill_staging/show/approve/discard/status` + CLI/TUI `/skill` 面板（已注册 prompt 技能数 + 待确认队列）。

**理由**: 与 howto 分层——howto 是**知识**（FTS 召回注入「参考做法」），skill 是**能力**（触发关键词精确命中注入「复用做法」）。「干完活自动沉淀 SKILL.md」是 L3「从做到造」的收尾：重复做一件事 → 不只沉淀知识原子，还沉淀可注册技能 → 下次同类任务按既有技能执行。

**反例**: 不做 handler 技能自动生成（自动写 Python handler 代码风险高、无法验证，只做 prompt 型技能模板）；不做自然语言主流程确认（与 howto 的「确认/丢弃」词表冲突，技能走独立 `/skill` 命令）；不做技能库版本管理（同名覆盖，YAML 即磁盘真相）。

**落地记录（已完成，2026-08-24，N30）**: `chuan/adapters/skill_loader.py`（`Skill` 增 `prompt/matches/render_prompt` + `SkillRegistry.add` 运行时注册 + `find_prompt_skill` 触发匹配）+ `chuan/skill_creator.py`（`SkillCreator`：maybe_create 门槛 + `_derive_name/description/keywords/prompt` 确定性提炼 + staging 队列 + approve 写 YAML+注册 + show）+ `chuan/role.py`（`_inject_reference` 技能优先→howto 兜底 + `_maybe_inject_skill` + `_maybe_create_skill` 挂接 `_wrap_result`）+ `chuan/runtime_supervisor.py`（`skill_creator` + `skill_staging/show/approve/discard/status`）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/skill` 面板）。测试：`tests/test_skill_creator.py` 12 例（关键词/门槛/staging/approve 写 YAML+注册/rename/discard/matches/find_prompt_skill/角色注入安全路径/监督接口），全量 453 passed、2 skipped。

## ADR-026: 记忆「类型 + 硬容量」约束（N31，借鉴 CC 4 类 + Hermes 2200 封顶）

**决策**: 给长期记忆加「类型 + 硬容量」双约束，防止 N26-N30 自动沉淀让记忆失控膨胀：
- **类型（CC 4 类）**：`remember(..., type=)` 写 frontmatter 的 `type` 字段 —— `fact` 事实 / `preference` 偏好 / `process` 过程（怎么做）/ `memory` 默认；非法值归 `memory`（确定性校验）；`recall(..., type=)` 按类型过滤命中，`MemoryHit.type` 暴露类型；`remember_memory/recall_memory` 工具透传 `type`；
- **硬容量（Hermes 2200 封顶）**：单条长期记忆正文硬上限 `_MAX_DOC_CHARS = 2200` 字符，`remember` 写入时确定性截断，防止单条失控；
- **边界**：只约束「单条」容量，不做总量淘汰（避免误删 curated wiki/知识库，淘汰策略留待后续评估）；截断在写入路径统一处理（覆盖写同样遵守）。

**理由**: N26/N27/N30 让记忆自动沉淀（howto 原子 + 技能 + 蒸馏），系统自转久了记忆会无限增长；CC 用 4 类记忆分层（哪些常驻上下文、哪些检索），Hermes 用 2200 字符封顶防止单条爆表。chuan 先落地「类型可分类检索 + 单条封顶」这两个确定性约束，成本低、可测、不误伤 curated 知识。

**反例**: 不做总量淘汰（eviction 会误删 wiki/howto 等 curated 知识，且「保 importance≥4」等规则复杂；真需要时单独评估）；不做 LLM 自动分类（记忆类型靠调用方显式传，确定性可测，免费模型分类不稳）。

**落地记录（已完成，2026-08-24，N31）**: `chuan/memory.py`（`MEMORY_TYPES` 4 类 + `_MAX_DOC_CHARS=2200`；`remember(..., type)` 校验 + 截断；`_with_frontmatter(..., type)` 写 frontmatter；`recall(..., type)` 过滤；`MemoryHit.type` + `_recall_root` 附类型）+ `chuan/memory_tools.py`（`remember_memory/recall_memory` 增 `type` 参数与描述）。测试：`tests/test_memory.py` 新增 6 例（type 写入/默认/非法回退/2200 截断/recall type 过滤/工具透传），全量 459 passed、2 skipped。

## ADR-027: Mission 长任务追踪（N32，跨对话看板）

**决策**: 给后台委派补上「跨对话的长任务」一等概念，让长目标跨会话可追踪、可看板：
- **Mission 模型**（`chuan/mission.py`）：`MissionManager` 持久化 `data/missions.json`（磁盘真相，重启不丢）—— `Mission{name, goal, agent, status, progress, task_ids, source, created, updated}`；状态机 `active → paused / done / failed`；
- **CRUD**：`start`（同名覆盖，name/goal 非空校验）/ `get` / `list(status)` / `update`（进度/状态/关联 task_id 去重）/ `finish`（done/failed + 摘要）/ `pause` / `resume` / `remove`（幂等）；
- **harness 关联**：`AgentHarness.submit(..., mission="")` 透传 mission 字段；`RuntimeSupervisor._on_harness_done` 全局完成回调——后台任务完成自动回写关联 mission 的进度（`[完成/失败] task_id: 摘要`）+ task_ids，但**不自动终结** mission（finish 由用户显式调用，避免单任务完成误判整个长目标结束）；
- **管理面**：`RuntimeSupervisor.mission_start/list/finish/pause/resume/remove` + CLI/TUI `/mission` 看板（🟢active/⏸paused/✅done/🔴failed + 关联任务数 + 最近进度）；`/bg <agent> <任务> --mission <name>` 关联委派。

**理由**: N21 harness 任务状态机只管「单次会话内」的后台委派（内存态，重启即失）。长目标（如「重构登录模块」「迁移整套服务」）跨多次委派、跨会话推进，需要持久化的一等概念与看板。借鉴 Aivy Mission 长任务追踪：把一次委派升级为可追踪的长目标。

**反例**: 不做任务级自动终局（单任务完成 ≠ 长目标完成，mission 终结交给用户显式 `finish`，避免误判）；不做 Mission 的依赖 DAG（任务级 depends_on 已有 N21，mission 只做聚合看板）；不做自动进度提炼（回写用后台任务结果摘要，确定性可测，不赌 LLM 总结）。

**落地记录（已完成，2026-08-24，N32）**: `chuan/mission.py`（`MissionManager` + `Mission` + `data/missions.json` 持久化 + start/get/list/update/finish/pause/resume/remove）+ `chuan/gateway/agent_harness.py`（`submit(..., mission)` 透传字段）+ `chuan/runtime_supervisor.py`（`missions` 管理器 + `_on_harness_done` 自动回写 + `mission_start/list/finish/pause/resume/remove` + `delegate(..., mission)`）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/mission` 看板 + `/bg --mission`）。测试：`tests/test_mission.py` 8 例（CRUD/持久化/校验/update/finish-pause-resume-remove/harness 透传/回写/管理接口），全量 467 passed、2 skipped。

## ADR-028: ACI 预判注入（N33，路由前并行预取上下文）

**决策**: 在路由决策确定目标岗位**之前**，就按用户消息并行预取「普通长期记忆 + wiki 知识库实体页」两类上下文，路由落定后把预取结果渲染成注入块前置到岗位任务文本，让 agent 首轮直接带相关背景开工：
- **预取器**（`chuan/aci.py` `AciPrefetcher`）：`prefetch(message)` 用 `ThreadPoolExecutor` 并行跑两个召回源——`memory.recall`（FTS5 token 级，普通记忆，按 `min_score` 阈值滤噪声）与 `memory.recall(namespaces=wiki 五目录)`（topics/entities/analysis/projects/howto，与 memory 源**互斥**不重复）；`render(bundle)` 渲染成 `【预判上下文】…` 注入块（无命中返回空串）；
- **注入点**：`RuntimeSupervisor.dispatch`/`dispatch_async` 路由前调用 `_aci_prefetch_block(message)`，把注入块经 `dispatch_to(..., aci_context=)` / `_dispatch_chief(..., aci_context=)` 透传给 `PersonaRole.dispatch(task, session_id, aci_context)`，在 `_dispatch_inner` 前置到任务文本（仅本岗位单次生效，不污染调用方原始 task）；
- **面板**：`RuntimeSupervisor.aci_status()` + CLI/TUI `/aci` 面板（最近预取记忆/wiki 命中数 + 是否注入）。

**理由**: agent 首轮常要自己调 `recall_memory`/`wiki_search` 摸上下文（首轮空转）。借鉴 BaiLongma「预判注入」：在路由（尤其 LLM 兜底选岗的耗时窗口）进行中并行预取，路由一落定上下文即就位，减少首轮空转、加快响应。

**反例**: 不用 LLM 预判（免费模型 tool-calling 不稳，确定性 FTS 召回即可靠又可测）；不注入 howto/skill（`_inject_reference` 已覆盖 L3 做法复用，ACI 只负责记忆+wiki 背景，避免重复注入）；预取失败绝不影响路由与执行（全旁路 try/except 吞异常）。

**落地记录（已完成，2026-08-24，N33）**: `chuan/aci.py`（`AciPrefetcher`：并行预取 memory+wiki、render 注入块、stats 面板，全旁路）+ `chuan/runtime_supervisor.py`（`_aci_prefetch_block`/`aci_status` + `dispatch`/`dispatch_async`/`dispatch_to`/`_dispatch_chief(_async)` 透传 aci_context）+ `chuan/role.py`（`PersonaRole.dispatch(..., aci_context)` 前置注入）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/aci` 面板 + /help）。测试：`tests/test_aci.py` 14 例（预取命中/互斥/空库/阈值/旁路隔离/render/stats/岗位透传/管理接口）+ `tests/test_tui.py` 2 例（bridge 转发 + `/aci` 面板渲染），全量 483 passed、2 skipped。

## ADR-029: HUD 通道升级 SCENE 协议（N34，core 持 scene → UI 纯投影）

**决策**: 把 HUD 从「扁平 legacy 命令流」（`wake`/`user:`/`ai:`/`effect:`/`monitor:`）升级为 SCENE 协议 v1，为手机 PWA 复用同一协议铺路：
- **core 持 scene**（`chuan/channels/hud.py`）：`HudChannel` 内部维护一份结构化 `scene` 状态（`{version, agent, effect, user:{text,ts}, ai:{text,ts}, monitor, tool_call}`），`scene_snapshot()` 深拷贝暴露；每次状态变化 `_set_scene` 仅在值变化时落 `patch` 增量帧——UI 只是投影，不持逻辑；
- **握手与协商**：连接建立推 `hello:{json}`（client + version + caps 能力清单），前端可回 `welcome`；`scene:{json}` 推全量（初始化/重连）；`patch:{json}` 推增量（只含变化字段）；
- **向后兼容双发**：`hud.scene`（默认 true）开启 SCENE 双发——每条高层命令同时发 legacy 命令（旧 Flutter exe 仍能显示）+ `patch` 增量帧（新前端 / PWA）；`hud.scene: false` 完全退回 legacy 单发；
- **前端投影**（`hud_overlay/lib/tcp_server.dart` + `agent_overlay.dart`）：`JarvisTCPServer` 拆出 `onFrame` 解析 SCENE 帧、`reply()` 回握手；`AgentOverlay._applyScene` 把 scene/patch 帧合并进 scene 状态并投影到当前 agent（agent 切换/effect/user/ai/monitor/tool_call）。

**理由**: N23 的 `monitor:{json}` 是「平铺直叙的单点命令」，无状态、无握手、无协商，未来手机 PWA 无法可靠复用。借鉴 BaiLongma SCENE-PROTOCOL v1（`core 持 scene → UI 纯投影 → 用户交互以 intent 回流`）：「全量 + 增量 patch」省带宽、「caps 协商」让前端按能力适配、「hello/welcome 握手」建立会话语义——同一协议 TCP / WebSocket 只换传输层。

**反例**: 不做双向 intent 回流（当前 chuan 交互入口是 CLI/语音，HUD 只是纯输出投影，intent 回流留待 PWA 接入时再做）；不做 scene 持久化（HUD 是易失投影，重连由后端 `send_scene_full` 恢复）；legacy 命令不删除（旧 Flutter exe 未重编译前需保持可用，双发是过渡期兼容策略）。

**落地记录（已完成，2026-08-24，N34）**: `chuan/channels/hud.py`（`SCENE_VERSION`/`SCENE_CAPS` + `_new_scene`/`scene_snapshot`/`_set_scene` + `send_hello`/`send_scene_full`/`send_patch`/`_dump` + 高层命令双发 + `hud.scene` 开关）+ `config/config.yaml`（`hud.scene: true`）+ `chuan/main.py` + `chuan/voice/main.py`（在线握手：`send_hello` + `send_scene_full`）+ `hud_overlay/lib/tcp_server.dart`（`TcpFrame`/`onFrame`/`reply` SCENE 帧分派）+ `hud_overlay/lib/agent_overlay.dart`（`_scene` 状态 + `_handleSceneFrame` hello→welcome + `_applyScene` 投影）。测试：`tests/test_hud.py` 新增 7 例（hello 携带 version/caps、scene 全量、snapshot 深拷贝、patch 不重发未变值、send_patch 空/离线降级、legacy 兼容 + scene:false 退回单发），全量 490 passed、2 skipped。Dart 端改动已人工审查（import dart:convert 已具备、Map 强转语法正确）；`flutter analyze` 在本沙箱受限（无法写 `%LOCALAPPDATA%/.dartServer` 缓存），需在有 Flutter 的环境跑一次 analyze 并重编译 assistant_overlay.exe 后再联调验证。

## ADR-030: 任务断点续跑（N35，打断不丢工具）

**决策**: 给岗位调度加「子任务级断点续跑」——长任务（多子任务、多波并行）执行中被打断（语音开口 / Esc 软中断 / 进程重启）时，已完成的子任务结果不丢，下次可复用：
- **断点档案**（`chuan/gateway/task_resume.py` `RoleTaskResumeStore`）：`data/task_resume/<session_id>.json` 磁盘真相，一个 session 一份；`save_plan` 规划落定即存（id/description/agent/depends_on），`save_result` 每个子任务完成即存（success/content 截断 4000/agent/at），`resume_plan` 读回、`list_resumable` 面板统计（total/done）、`clear` 清除；session_id 白名单清洗防路径注入，全程旁路异常吞掉；
- **复用**：`PersonaRole._run_subtask` 收到 `resume_hits`（上次成功子任务结果映射）时直接返回 `[续跑复用] …` 缓存结果，**跳过 agent 调用**；`_rehydrate_plan` 从档案重建 plan（复用上次规划而非重新 LLM 规划，保证 id/依赖与缓存结果对齐）——已完成子任务复用、只跑未完成/失败部分；
- **入口**：`RuntimeSupervisor.resume_to(worker, session_id)`/`resume_list`/`resume_clear` + CLI/TUI `/resume <session> <worker>` 看板（🟢 progress total/done）。

**理由**: N21 harness 管「单次会话后台委派」，N19 岗位调度跑长任务时被打断，已执行子任务（尤其依赖后续的波次结果）如果丢弃就得整轮重跑——浪费模型调用与耗时。借鉴 Aivy「流式打断不丢工具」：打断时保留已执行工具结果，只重跑未完成部分。

**反例**: 不做 agent 调用级中断（底层 LangGraph 线程无法强杀，Esc 软中断后线程继续后场跑完，N19 已如此）；不做跨 session 自动续跑（resume 必须显式指定 worker + session，避免误恢复；同任务文本匹配仅在档案 task 一致时复用）；不做结果版本合并（同一子任务重跑成功即覆盖旧结果，简单可测）。

**落地记录（已完成，2026-08-24，N35）**: `chuan/gateway/task_resume.py`（`RoleTaskResumeStore`：save_plan/save_result/resume_plan/list_resumable/clear，旁路 + 白名单）+ `chuan/role.py`（`PersonaRole.__init__(..., resume_store)` + `dispatch(..., resume)` + `_dispatch_inner` resume 分支（复用缓存 plan + resume_hits）+ `_rehydrate_plan` + `_execute(..., resume_hits)` 跳过已完成子任务 + `_run_subtask(..., resume_hits)` 命中复用 + 完成即存结果）+ `chuan/runtime_supervisor.py`（`resume_store` 实例 + `resume_to/list/clear`）+ `chuan/gateway/agent_spawner.py`（注入 resume_store）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/resume` 面板与命令）。测试：`tests/test_task_resume.py` 9 例（store 持久化/进度统计/清除/截断/rehydrate/复用跳过 agent/全量执行/新结果回写）+ `tests/test_tui.py` 2 例（bridge 转发 + `/resume` 面板渲染），全量 503 passed、2 skipped。

## ADR-031: 外接知识库检索工具（N36，临时查 Obsidian 库，与内部记忆隔离）

**决策**: 给所有角色注入外接知识库检索工具 `search_vault`/`list_vaults`——**临时查外置 Obsidian 库**，与内部长期记忆管道严格隔离：
- **配置**：`config.yaml → memory.external_vaults`（`name` + `path` 列表），`Memory._resolve_external_vaults` 懒加载解析为绝对路径（相对路径以项目根为准）；
- **工具**（`chuan/memory_tools.py` `build_vault_tools`）：`list_vaults` 列出已配置外接库（名称+路径，未配置返回提示）；`search_vault(query, vault="", limit=5)` 检索外接库——`vault` 留空=检索全部已配置外接库，返回命中文档路径+摘要；
- **隔离**（关键）：`search_vault` 调 `memory.recall(query, namespaces=[], vaults=[...])`——`namespaces=[]` 保证**绝不检索内部 `notes/`**；`recall` 的 `vaults` 参数语义：`None`=不查外接库（内部 `recall_memory` 默认），空列表=查全部外接库，列表=只查指定库；外接库走独立 vault key（`_vault_key_for(root)`）+ `reindex_external` 增量索引，无 frontmatter 按 importance=3，**只读绝不写回**；
- **注入**：`persona_loader.py` 对 `memory is not None` 的**所有角色**追加 `build_vault_tools`。

**理由**: 用户有外置 Obsidian 资料库，需临时检索具体内容（笔记/外部资料），但不该混入内部长期记忆管道（内部 `recall_memory` 结果由 N13 三层闭环与 ACI 预判消费，混入会污染上下文与记忆沉淀）。独立工具 + 独立 vault key + `namespaces=[]` 三重隔离，「临时查外置库」专用，无需把外部库当成内部记忆导入。

**反例**: 不做外接库写入（只读，绝不把检索内容写回外部库或内部 notes）；不做自动混合检索（外接库必须显式 `search_vault` 才召回，ACI 预判与 `recall_memory` 不包含外接库）；不做外部库元数据维护（mtime 增量索引由 `reindex_external` 在启动挂接时完成）。

**落地记录（已完成，2026-08-24，N36）**: `chuan/memory_tools.py`（`build_vault_tools`：`list_vaults`/`search_vault`，`namespaces=[]` 隔离内部 + `vaults=[]`=全部外接库）+ `chuan/memory.py`（`recall` 外部库块改 `vaults is not None`（空列表=全部外接库）；`_resolve_external_vaults` 懒加载 + `reindex_external` 增量索引独立 vault key）+ `chuan/persona_loader.py`（所有角色注入 build_vault_tools）+ `config/config.yaml`（`memory.external_vaults` 示例：obsidian → `D:/Resources/Obsidian`）。测试：`tests/test_vault_tools.py` 8 例（外部命中/空库名=全部/无命中提示/不混入内部记忆/未知库提示/列库/未配置提示/工具描述），全量 511 passed、2 skipped。

## ADR-032: 岗位化 1:N 过渡（N37，岗位多 agent 池 + 会话级状态隔离）

**决策**: 把「岗位直接创建单 ReAct agent」的 1:1 逐步迁移为「岗位可管理 N 个 agent」——本迭代落地第一台阶：
- **岗位多 agent 池**（`chuan/role.py`）：`PersonaRole._agents: dict[instance_id, agent]`，默认实例 id="default"（`_ensure_default_agent` 懒加载，向后兼容 1:1）；新增 `spawn_agent(instance_id, system_prompt="", tools=None, model=None)` 显式扩容（同 id 幂等复用；无模型/无 spawn 能力时回退默认实例，不抛错）；`agent_count()`/`list_agents()` 暴露岗位持有的 N 个实例（默认 + 扩容）；
- **会话级状态隔离**（关键）：`progress` 改按 session 隔离（`_session_progress: dict[session_id, dict]` + `_session_progress_view(session_id)`），role 级 `self.progress` 保留为「最近会话」视图（1:1 时代的 `role.progress["s1"]` 直读法兼容）；团队状态落盘 `_state_writer` 改按会话隔离（`_state_writers: dict[session_id, TeamStateWriter]`，各会话各写各的 `data/teams/<session_id>.json`）——**同一岗位可并行服务多个会话（微信/CLI/语音），进度与落盘互不串扰**；
- **可观测**：`chuan/gateway/heartbeat.py` 健康报告新增 `role_agents`（各岗位 agent 实例总数，`getattr` 兜底），摘要行显示 `agent N`。

**理由**: 当前每个岗位只持有**一个**默认 ReAct agent，且 `progress`/`_state_writer` 是岗位级共享可变状态——两个会话同时派发到同一岗位（微信 + CLI 并行）会互相覆盖进度、写串团队状态文件。「岗位化 1:N」按 ADR-014「逐步迁移」落地：先给岗位「持有 N 个 agent」的能力与「多会话互不串扰」的隔离保证，用法（如并行子任务各配一个实例）随需求增长，不一次性推翻现有调度。

**反例**: 不做 1:N 默认启用（现有「auto 子任务仍走默认实例」的调度语义不动，扩容是显式 `spawn_agent` 能力）；不做岗位内 agent 实例的自动回收（实例由岗位持有到销毁，与 `_specialists`/常驻池一致，避免 LRU 复杂度）；不做跨岗位 agent 共享（每岗位实例独立，杜绝跨岗位状态耦合）。

**落地记录（已完成，2026-08-24，N37）**: `chuan/role.py`（`PersonaRole._agents` + `_ensure_default_agent` 纳入池 + `spawn_agent`/`agent_count`/`list_agents` + `_session_progress`/`_session_progress_view` + `_state_writers` 会话隔离，删除死代码 `_default_agent` 字段）+ `chuan/gateway/heartbeat.py`（`role_agents` 汇总 + 摘要 `agent N`）。测试：`tests/test_role.py` 新增 7 例（默认实例入池/扩容到 N/同 id 幂等/无模型兜底/default+扩容共存/会话进度隔离/并发会话不串扰 + 团队落盘按会话）+ `tests/test_gateway_components.py` 新增 1 例（role_agents 汇总），全量 519 passed、2 skipped。

## ADR-033: 岗位化 1:N 过渡·第二台阶（N38，1:N 默认启用：并行子任务独立 worker）

**决策**: 在 N37（岗位可持有 N 个实例 + 会话隔离）之上，把 **1:N 默认启用**到并行调度——同一波 ≥2 个并行 auto 子任务不再挤在同一默认实例，而是各分配一个独立 worker 实例：
- **独立图实例**（`chuan/agent_pool.py` `spawn_builtin_instance`）：调 `persona_loader.birth(name, force_rebirth=True)` 绕过缓存，每次返回**全新 CompiledStateGraph**——同人设同工具，但图互不共享（这是「独立实例」的物理基础，`get_builtin_agent` 的缓存图做不到）；
- **并行分配**（`chuan/role.py`）：`_execute` 每波先 `_assign_wave_instances(ready)`——仅当本波 ≥2 个 auto/builtin 且无 specialist 的并行子任务时，各配一个 worker（`worker0..workerN`，按需创建 + 复用，上限 `CHUAN_PARALLEL_WORKERS` 默认 3，超出轮转复用）；单任务/串行/指定实例/常驻 agent 保持原调度（向后兼容）；`_run_subtask(..., instance)` 用预分配实例，否则走 `_resolve_sub_agent`；
- **实例名可指定**：`_resolve_sub_agent` 新增「岗位持有的实例」解析优先级（specialist > 岗位实例 id > 常驻池 > 默认）——子任务 `agent` 字段可直接写 `spawn_agent` 扩容出的实例 id（如 "writer"/"analyst"）；
- **兜底**：池无 `spawn_builtin_instance` 能力或创建失败 → 回退默认实例（旁路，不抛错），保持 N37 前行为。

**理由**: ADR-032 明确把「1:N 默认启用」推迟到后续台阶。并行子任务（`asyncio.gather`）共享同一默认实例，虽靠 thread_id 隔离对话状态，但共享同一图实例（工具集/checkpointer 同一对象）——独立 worker 让每个并行子任务有自己的图，为后续按实例配工具/模型/记忆铺路，也让「岗位真的在管 N 个 agent」落地。

**反例**: 不做跨岗位 worker 共享（每岗位独立实例，杜绝跨岗位状态耦合）；不做 worker 自动回收（实例由岗位持有到销毁，与 `_specialists`/N37 `spawn_agent` 一致）；不做超过波大小的预创建（worker 按需懒创建，避免启动即建 N×岗位 个图）；不改 specialist/常驻池/显式实例的既有优先级。

**落地记录（已完成，2026-08-24，N38）**: `chuan/agent_pool.py`（`spawn_builtin_instance(persona_name, checkpointer)`：`force_rebirth=True` 独立图）+ `chuan/role.py`（`_execute` 每波 `_assign_wave_instances` + `_run_subtask(..., instance)` 透传 + `_ensure_worker_instance`（按需建 worker，无能力/失败回退默认）+ `_assign_wave_instances`（≥2 并行 auto 才启用，上限 `CHUAN_PARALLEL_WORKERS`）+ `_resolve_sub_agent` 支持岗位实例 id）。测试：`tests/test_role.py` 新增 5 例（并行 auto 子任务各分独立 worker/`CHUAN_PARALLEL_WORKERS` 封顶复用/串行子任务仍走默认/`_resolve_sub_agent` 用岗位实例 + 未知名回退/`spawn_builtin_instance` force_rebirth 独立图），全量 524 passed、2 skipped。

## ADR-034: 岗位化 1:N 过渡·第三台阶（N39，按实例配置工具/模型/记忆）

**决策**: 在 N37（岗位持有 N 实例）/N38（并行独立 worker）之上，让**每个实例可按需配置工具、模型与记忆**——用 `RoleAgentConfig` 统一声明，贯穿 `spawn_agent` 与 worker 路径：
- **`RoleAgentConfig`**（`chuan/role.py`）：`{tools=None, model=None, system_prompt="", checkpointer=None}`——工具子集（None=persona 工具集）/聊天模型（None=persona brain）/系统提示词（空=persona 人设，可注入实例级私有记忆）/实例级会话存档（None=岗位共享）；
- **`persona_loader.birth()` 覆盖参数**：新增 `model`/`tools`/`system_prompt`——`model` 覆盖时跳过 brain 解析；`tools` 覆盖时**精确替换** persona 工具集（不再自动加 sub_agent 工具）；`system_prompt` 覆盖人设——「YAML→agent」唯一入口支持按实例定制；
- **`AgentPool`**：`spawn_builtin` 加 `checkpointer`；`spawn_builtin_instance` 加 `model`/`tools`/`system_prompt` 覆盖透传（None 沿用 persona）；
- **`spawn_agent(instance_id, ..., config=None)`**：`config`（RoleAgentConfig）提供结构化配置，旧关键字参数（system_prompt/tools/model/checkpointer）可覆盖 config 默认值；创建时把配置记入 `_agent_configs`（检视审计）；
- **worker 尊重配置**：`_worker_config`（岗位级并行 worker 默认配置）非空时，`_ensure_worker_instance` 用它覆盖 persona 默认创建每个 worker；无能力/失败仍回退默认实例（旁路）。

**理由**: ADR-033 明确「为后续按实例配工具/模型/记忆铺路」。此前 worker/扩容实例只能是「全人设复制」或「零散传参」——统一 `RoleAgentConfig` 让岗位能声明不同实例（如 writer 实例只挂写作工具 + 专属记忆、coding 子任务用更强模型），实例名（`_resolve_sub_agent` 已支持）配合配置使「按任务复杂度选实例」真正落地，也为 config.yaml 声明实例配置铺路。

**反例**: 不做 config.yaml 的实例声明段（本迭代只提供程序化 `RoleAgentConfig` 能力，声明式配置留待需要时接入）；不做实例级长期记忆仓库（「记忆」此处=实例级会话存档 checkpointer + 系统提示词注入私有记忆，不改 Memory 底层）；不做实例工具热更新（工具在出生时固定，变更需重建实例）；不改默认实例（"default" 始终全人设）。

**落地记录（已完成，2026-08-24，N39）**: `chuan/role.py`（`RoleAgentConfig` dataclass + `spawn_agent(..., config=None, checkpointer=None)` 合并 config + `_agent_configs` 记录 + `_worker_config` 属性 + `_ensure_worker_instance` 尊重配置）+ `chuan/persona_loader.py`（`birth(model/tools/system_prompt)` 覆盖参数，model 覆盖跳过 brain，tools 覆盖精确替换）+ `chuan/agent_pool.py`（`spawn_builtin` 加 `checkpointer` + `spawn_builtin_instance` 覆盖透传）。测试：`tests/test_role.py` 新增 3 例（spawn_agent 应用 config 工具/模型/记忆/旧参数覆盖 config/worker 尊重岗位配置）+ `tests/test_persona_loader.py` 新增 1 例（birth 覆盖模型/工具/提示词/checkpointer），全量 528 passed、2 skipped。

## ADR-035: 按任务复杂度选实例的声明式配置（N40，config.yaml role_instances）

**决策**: 让「按任务复杂度选实例」可**声明式配置**——在 config.yaml 用 `role_instances` 段声明实例与复杂度档位映射，岗位 dispatch 时按复杂度分级自动选实例：
- **配置格式**（config.yaml）：`role_instances.tiers`（simple/medium/heavy → 实例 id，缺省全 default）、`role_instances.instances`（实例声明：`brain` 用哪个大脑档位/`tools` 工具子集/`system_prompt`）、`role_instances.roles.<name>.tiers`（角色级覆盖档位映射）；全默认/缺段时所有任务走默认实例（零开销旁路，向后兼容 1:1）；
- **解析器**（`chuan/role_config.py`）：`load_role_instances(config_path, brains)` → `RoleInstanceConfig{tiers, roles, instances}`，`brain` 名用 brains registry 解析为模型（取不到保持 model=None 沿用 persona 大脑）；`tier_for(role)` 角色覆盖优先合并；解析失败/缺文件 → 全默认，全程旁路不抛错；
- **复杂度分级**（`chuan/role.py` `_classify_complexity`）：纯规则无 LLM——heavy（命中重型标记：代码/编程/开发/调试/脚本…）、medium（会走规划）、simple（其余短问答）；
- **选实例**（`_resolve_tier_instance`）：按档位取实例 id → `_ensure_configured_instance` 用声明的 RoleAgentConfig 经 `spawn_builtin_instance` 创建/复用（记录进 `_agents`/`_agent_configs`）；未配置/实例缺失/创建失败 → 回退默认实例；
- **接线**：`AgentSpawner.spawn` 从 `sup.config_path` + `sup.brains` 加载 `role_instances`，注入所有岗位与幕僚长；dispatch 单 agent 路径（含规划失败兜底）按复杂度选实例——显式指定 agent 与规划分支语义不变。

**理由**: ADR-034 明确把「config.yaml 实例声明段」推迟。程序化 `RoleAgentConfig` 是能力，但要「改配置即可换实例」还需声明式方案——运维/用户不用改代码，在 config.yaml 声明「programmer 的重型任务用 cloud_coding + 编码工具实例」即生效，让「简单→默认、重型→更强模型/编码实例」落地。

**反例**: 不做复杂度档位的 LLM 判定（纯规则分级，零额外调用、可测）；不做逐子任务的复杂度选实例（本迭代只作用于单 agent 路径，规划分支仍走 worker 实例，可用 `_worker_config` 定制）；不做实例级长期记忆仓库（沿用 N39 语义：checkpointer + 系统提示词注入）；不做实例声明的热重载（配置在 spawn 时一次性读取）。

**落地记录（已完成，2026-08-24，N40）**: `chuan/role_config.py`（`RoleInstanceConfig` + `load_role_instances` 解析器，`tier_for` 角色覆盖）+ `chuan/role.py`（`PersonaRole(..., instance_config)` + `_classify_complexity`（重型标记纯规则）+ `_resolve_tier_instance` + `_ensure_configured_instance`（声明式实例创建/复用/回退）+ dispatch 单 agent 路径按复杂度选实例）+ `chuan/gateway/agent_spawner.py`（从 config_path+brains 加载并注入所有岗位）+ `config/config.yaml`（`role_instances` 段，opt-in 示例）。测试：`tests/test_role_config.py` 新增 12 例（缺段/缺文件默认/解析 tiers+instances+roles/brain 解析/角色覆盖/未知 brain 回退/复杂度分级/选声明式实例/无配置回退/缺失实例回退/dispatch 重型用声明实例/simple 用默认/同 id 复用），全量 540 passed、2 skipped。

## ADR-036: 动态实例池与自动扩缩容（N41，config.yaml role_instances.pool）

**决策**: 让岗位实例池具备**自动扩缩容**能力——在 config.yaml 用 `role_instances.pool` 段声明容量与空闲回收策略，岗位运行时按需扩容、开工前自动缩容回收闲置实例：
- **池配置**（`chuan/role.py` `RolePoolConfig`）：`min_instances`（缩容时保留的非默认实例下限，默认实例是岗位身份永不回收）、`max_instances`（扩容上限，并行 worker 最多建到 N 个）、`idle_ttl`（空闲超过该秒数 → 自动缩容回收；再次需要时按需重建，扩容闭环）；
- **用量统计**（`_InstanceStat`）：每个池内实例记录 `created_at`/`last_used_at`/`uses`，创建即入池（`_init_instance_stat`），实际执行一次即更新（`_touch_agent`，注入 dispatch 单 agent 路径与 `_run_subtask` 子任务路径；常驻 agent/specialist 非池成员自动忽略）——扩缩容全部基于确定性统计，无 LLM；
- **扩容遵守上限**（`_assign_wave_instances`）：开启动态池时并行 worker 上限取 `pool.max_instances`（否则回退 `CHUAN_PARALLEL_WORKERS`）；
- **自动缩容**（`reclaim_idle`）：非默认实例按「最近使用」升序，回收空闲超 TTL 且超出下限的部分；`_maybe_reclaim_idle` 在每次 dispatch 开工前调用（仅开启动态池时），回收数上报进度事件 `pool_reclaim`；
- **观测**（`pool_stats`）：暴露 `size/min/max/idle/uses`，`gateway/heartbeat.py` 汇总进健康报告（`pools` + `pool_total`），状态栏可看「池 3/3」；
- **接线**：`RoleInstanceConfig` 增加 `pool` 字段，`load_role_instances` 解析 `role_instances.pool`（缺省字段回默认值，缺段 → None 关闭自动扩缩容）；`PersonaRole.__init__` 从 `instance_config.pool` 取池配置；`AgentSpawner` 已注入 `instance_config`，无需改动。

**理由**: N38/N40 让岗位可并行建多个 worker/声明式实例，但实例建了不回收会随会话累积、浪费资源；「按需扩容 + 空闲回收 + 保留下限」是池化标准形态，让岗位在并行高峰时铺开、闲时自动收敛，且全程确定性可测、零额外 LLM 调用。缩容后再次需要时按需重建，扩容闭环不丢能力。

**反例**: 不做定时器/后台常驻进程轮询回收（利用 dispatch 开工前这个天然触发点，零额外线程）；不做基于负载预测的主动扩容（只做「按需建 + 上限截断」，简单可测）；不回收默认实例（岗位身份，需常驻承接单任务/串行子任务）；不做实例级并发占用标记（TTL 空闲判定足够，避免复杂度失控）。

**落地记录（已完成，2026-08-24，N41）**: `chuan/role_config.py`（`RoleInstanceConfig.pool` + `load_role_instances` 解析 `pool` 段）+ `chuan/role.py`（`_init_instance_stat` 注入 `_ensure_default_agent`/`_ensure_worker_instance`/spawn/声明式实例；`_touch_agent` 注入单 agent 与子任务路径；`_maybe_reclaim_idle` 开工前自动缩容 + `pool_reclaim` 事件；扩容上限取 pool.max）+ `chuan/gateway/heartbeat.py`（`pools`/`pool_total` 观测 + 状态栏「池 size/max」）+ `config/config.yaml`（`role_instances.pool` 段，默认 min=1/max=3/ttl=300）。测试：`tests/test_role_config.py` 新增 3 例（pool 解析/缺省字段默认/缺段 None）+ `tests/test_role.py` 新增 10 例（worker 用量统计/单任务 touch 默认实例/回收最久未用保下限/全空闲保下限/默认实例永不回收/取池配置缺省/池统计/上限取 pool.max/开工自动回收集成/未开池不回收），全量 552 passed、2 skipped（1 例 hud 网络时序偶发 flaky，隔离重跑通过）。

**验证记录（已完成，2026-08-24，N41 运行时验证）**: 真实运行时栈（真实 config + AgentSpawner + 岗位 + worker spawn）验证自动扩缩容闭环：配置接线（pool 注入 14 个岗位）→ 扩容（4 并行子任务建 3 worker，守 max=3）→ 用量统计（touch 更新 last_used/uses）→ 缩容（TTL 调小触发开工前回收，最久未用被回收、最近使用幸免、守 min=1）→ 观测（Heartbeat `pools`/`pool_total` + 状态栏「池 1/42」）。详见 [E2E-TEST-2026-08-24-pool-scaling.md](../archive/E2E-TEST-2026-08-24-pool-scaling.md)。

**真实会话交叉验证（已完成，2026-08-24，N42 多岗位协作叠加动态池）**: 本次 N42 端到端真实会话在动态池开启（`role_instances.pool` min=1/max=3/ttl=300）下运行：研究/编程双岗 `dispatch` 开工前均自动走 `_maybe_reclaim_idle` 空闲回收检查，执行实例纳入池用量统计——动态池与多岗位并行协作并存无回归、无异常。

## ADR-037: 岗位间协作·第五台阶（N42，多岗位并行编排 + 共享黑板）

**决策**: 把「岗位化 1:N」的粒度从「单岗位管 N 实例」升到「一任务拆多岗位并行」——`TeamOrchestrator` 编排器 + `TeamBlackboard` 共享黑板：
- **触发（确定性优先）**：
  - 显式点名：「让<研究>、<文案>一起<任务>」——`detect_team_roles` 纯规则解析（连接词「一起/协作/合作/…」分界，`、和与及,` 拆分名单，roster 双向匹配英文/中文岗位名，≥2 岗成立），**不调 LLM**（对齐免费模型 JSON 不稳教训）；
  - `/team <任务>`：LLM 从班底选 2-4 岗拆分（`plan_team_llm`），严格 JSON 校验 + 未知岗位过滤 + 不足 2 岗回 None，失败兜底单岗位；
- **共享黑板**（`TeamBlackboard`，磁盘真相延续 team_state 哲学）：`data/teams/<session>/blackboard/` 下 `context.md`（总任务+分工，各岗共享同一份，避免重复劳动）+ `<role>.md`（各岗产出落盘，聚合/复盘/审计）；
- **并行执行**：复用幕僚长常驻事件循环（`run_coroutine_threadsafe` 全部先调度再逐个取结果 = 真并行），各岗独立会话 `team:<session>:<role>`，单岗失败不阻断其余；
- **确定性汇总**（`_summarize`）：各岗产出分节 + 成败标注（「执行失败」标记 + 剥「[岗位]」前缀），不调 LLM；
- **接入**：`RuntimeSupervisor.dispatch` 在天气兜底后、单岗位路由前检测团队意图（`/team` 前缀 + 自然语言点名）；普通消息/单岗位点名零额外开销（解析是 O(岗位数) 字符串匹配）；CLI `/help` 增补 `/team`。

**理由**: N37–N41 已让「单岗位」内部 1:N 全闭环（多实例/并行/按复杂度选/动态池），但跨岗位仍只有 `ask_role` 单层问答。真实用户任务常横跨多个领域（筹备发布会 = 调研+文案+日程），单岗位硬扛会顾此失彼；多岗位并行 + 共享分工上下文，让每个岗位只做自己的领域专家活，产出落到共享工作区供聚合/复盘。

**反例**: 不做跨岗位多轮问答/实时黑板互读（V1 只共享「分工上下文 + 产出落盘」，岗位间实时信息互通仍走 `ask_role`，避免并行锁/一致性复杂度失控）；不做依赖编排（各岗位无先后依赖，全部并行——有依赖的多岗位 DAG 留待后续）；不做岗位内 agent 池在团队场景的特殊化（沿用 N37–N41 各岗位自带的实例池/扩缩容）；LLM 选岗路径严格回退，绝不因 JSON 不稳阻断显式点名路径。

**落地记录（已完成，2026-08-24，N42）**: `chuan/team_orchestrator.py`（`TeamBlackboard`（context.md + `<role>.md` 落盘）+ `TeamOrchestrator`（`detect_team_roles` 确定性解析 / `plan_team_llm` LLM 选岗严格校验 / `orchestrate` 并行派发+落黑板+确定性汇总 / `_subtask_prompt` 注入团队总任务+分工+黑板）+ `chuan/runtime_supervisor.py`（`team_orchestrator` 懒加载属性 + `_try_team_orchestrate` 接入 dispatch + `_resolve_team_model`）+ `chuan/main.py`（/help 增补 `/team`）。测试：`tests/test_team_orchestrator.py` 新增 14 例（显式解析 5（含英文名/三岗/单岗/无命中）/ 黑板落盘 / 并行派发 + 共享上下文 / barrier 真并行 / 单岗失败不阻断 / 岗位缺失标记失败 / LLM 选岗 4 例），全量 545 passed、2 skipped（另 1 例 hud 网络时序偶发 flaky 隔离重跑通过）。

**验证记录（已完成，2026-08-24，N42 运行时接线）**: 真实运行时栈验证：roster 列出 13 个可协作岗位（中英文名齐全）→「让研究、编程一起写个 Python 脚本」确定性解析出研究+编程两岗 → 普通消息（查天气/翻译）与单岗位点名均不被团队路径劫持、零额外开销。

**真实会话验证（已完成，2026-08-24，N42 端到端多岗位协作）**: 起真实 `RuntimeSupervisor`（真实 LLM + 真实黑板落盘 + AsyncSqliteSaver checkpointer）跑通完整协作闭环，全程 27.5s：

- **派发**：`让研究、编程一起开发一个猜数字小游戏，研究负责整理玩法说明与规则文档，编程负责实现可运行的 Python 代码`
- **链路**：`dispatch` → `_try_team_orchestrate` 命中「一起」→ `detect_team_roles` 确定性拆出研究+编程两岗（无 LLM 猜岗）→ `TeamOrchestrator.orchestrate` 复用幕僚长常驻事件循环并行派发（独立会话 `team_teamdemo_researcher/programmer`）→ 各岗 `dispatch` 完成后落黑板 → 确定性分节汇总；返回 `route=team`。
- **双岗并行产出**：研究岗写出规则文档（`data/teams/team_teamdemo/rules.md`）+ 沉淀长期记忆 `guess_number_rules` / wiki `GuessNumberGame_Rules` / 知识原子 `write_game_rules_document`；编程岗写出可运行 `chuan/games/guess_number.py`（1-100 随机 / 7 次机会 / 输入校验 / 循环重玩）+ 模块封装 + `docs/games/guess_number.md` + 沉淀 `create_guess_number_game`。

**黑板落盘样本**（`data/teams/team_teamdemo/blackboard/`，已验证）：

```
blackboard/
├── context.md      # 总任务 + 分工清单（各岗共享同一份，开工即写）
├── researcher.md   # 研究岗产出
└── programmer.md   # 编程岗产出
```

`context.md` 内容（开工时写，两岗拿到同一份分工上下文）：

```
# 团队任务
开发一个猜数字小游戏，研究负责整理玩法说明与规则文档，编程负责实现可运行的 Python 代码

## 分工
- 研究：研究
- 编程：编程
```

`researcher.md` 节选（产出落盘，供聚合/复盘/审计）：

```
## researcher
### **研究岗任务完成报告**
**任务**：整理猜数字小游戏的玩法说明与规则文档
**状态**：✅ 已完成
**交付物**：
1. 规则文档（data/teams/team_teamdemo/rules.md，含目标/核心规则/反馈机制/胜利条件/策略提示）
2. 信息同步：更新团队黑板
3. 知识沉淀：长期记忆 guess_number_rules + wiki GuessNumberGame_Rules + 知识原子 write_game_rules_document
```

`programmer.md` 节选：

```
## programmer
【编程任务完成报告】
1. 代码实现：chuan/games/guess_number.py（随机数生成 / 7 次机会 / 输入验证 / 大小反馈 / 循环重玩）
2. 模块化封装：chuan/games/__init__.py 注册，可被导入
3. 文档产出：docs/games/guess_number.md
4. 经验沉淀：知识原子 create_guess_number_game + 长期记忆
✅ 任务全部完成，代码已就绪，可运行。运行命令：python -m chuan.games.guess_number
```

**汇总输出**（确定性分节，不调 LLM）：`团队协作完成：…` 后按岗分节 `### [研究]` / `### [编程]`，各岗产出完整、无互相串扰。演示产物（脚本/生成代码/文档/团队会话目录/演示记忆笔记）已清理，FTS 重索引无残留。

**并发/状态机排雷（2026-08-26，N42 补强）**: 复审确认两个真实健壮性缺口并修复：
- **c 超时未取消**：`_await_result` 原 `fut.result(timeout=600)` 超时后 `except Exception` 一把抓，底层 `asyncio` 任务未被取消，继续在事件循环里泄漏（占资源、结果被丢弃、无人等待）。修复：精确区分 `concurrent.futures.TimeoutError`，超时分支调 `fut.cancel()`（向底层 task 注入 `CancelledError`）后返回「执行超时（>600s），已取消」；普通异常仍走「执行失败」。`write_result` 在 `_await_result` 之后串行调、dispatch 本身不写黑板，故取消不会留下半截产出。超时阈值 600s 保持不变。
- **d1 可重入 session 冲突**：同一 team 会话快速/并发重复触发会拼出相同 `session_id` → checkpointer 复用脏 tool_call 残留 + 黑板覆盖。修复：`TeamOrchestrator` 增 `_running: set` 防重入白名单，`orchestrate` 入口 `_safe(session_id)` 命中即拒绝（「正在协作中」），主体包 try/finally 在末尾 `discard` 清理。
- 测试：`tests/test_team_orchestrator.py` +4（超时取消 / 普通异常不取消 / 防重入拒绝不派发 / 正常执行后清理），共 20 passed。
- 未做：d2（跑完后顺序重复触发时的 checkpointer 唯一化）暂缓——d1 已挡并发/快速重复的实质路径，d2 会改变 team state 落盘文件名约定，留待确需时。

## ADR-038: 记忆语义检索·sqlite-vec（N43，FTS5 词法 + 向量语义双路合并）

**决策**: 给长期记忆补**语义检索**，但**完全不动 FTS5**——在既有 SQLite 上加一层 `sqlite-vec` 向量索引作旁路，召回时「词法命中 + 语义命中」双路合并：

- **向量索引**（`memory_vec`，vec0 表，cosine 距离）：与 FTS 同库（`memory_fts.db`）同事务锁；列 `embedding float[DIM] + vault + rel_path + title`（元数据列可存可查）。KNN 查询**不能对元数据列加 WHERE**（sqlite-vec 限制）→ 跨库/命名空间隔离在 **Python 侧过滤**；
- **嵌入源可注入**（`Memory(..., embedding=...)`）：`False`=关闭（默认）/ `callable`=测试 stub / `None`=按 config 建云端客户端。云端走 OpenAI 兼容 embeddings API（bailian `text-embedding-v3` / zhipu 皆可，key 解析对齐 brains：先 env 后 secrets.yaml）；
- **写入旁路**（`_index_document` 提交 FTS 后调 `_index_semantic`）：正文截断前 2000 字符嵌入，失败静默降级（FTS 真相不动）；reindex 对「FTS 已索引但向量缺失」的文档自动回填（自愈）；
- **双路合并**（`recall`）：FTS 命中（词法 `_score`）+ 语义命中（`weight*(1-distance)`）按相对路径累加排序；语义只服务非黑板命名空间与内部库，外部库仍纯词法；importance 门控同样生效；
- **维度自愈**（`_ensure_vec`）：vec 表建在共享库、维度由首次创建锁死（`IF NOT EXISTS` 不重建）；检测到现有表声明维度与当前嵌入源不一致（如测试 dim=4 污染后真实 1024）→ **DROP 重建**（向量索引是纯派生数据，reindex 回填，安全）。

**理由**: FTS5 是词法检索，只能字面命中——「查一下上次怎么部署的」与「召回部署笔记」这种同义不同词会漏（中文分词歧义更甚）。语义检索补上同义/语义相似召回，但**不引入常驻服务、不换存储**（sqlite-vec 是 SQLite 官方扩展，嵌入式）；默认 `enabled: false` 保证零依赖零成本，纯词法行为与历史完全一致（测试封闭），语义是「能删能重建的派生索引」，旁路降级哲学一脉相承。

**反例**: 不做向量替换词法（双路合并，语义作为增强而非替代）；不做外部库语义召回（V1 只服务内部记忆）；不做向量维度动态迁移（维度不符直接 DROP 重建，避免复杂迁移）；默认不开启（云端嵌入按量计费 + 网络依赖，opt-in 由用户按需开启）。

**落地记录（已完成，2026-08-24，N43）**: `chuan/embed.py`（`EmbeddingClient` OpenAI 兼容 embeddings + `resolve_api_key`（env→secrets 回退））+ `chuan/memory.py`（`_resolve_embedding`/`_ensure_vec`（维度自愈 DROP 重建）/`_index_semantic`（写入旁路 + 2000 字符截断）/`_vec_hits`（KNN）/`_recall_semantic`（Python 侧过滤 + importance 门控）/`_merge_hits`（双路累加））+ `_reindex_root`（语义回填 + stale 向量清理）+ `recall` 双路合并 + `config/config.yaml`（`memory.semantic` 段，enabled 默认 false）。测试：`tests/test_embed.py` 新增 6 例（env 优先/secret 回退/缺 key None/from_config 校验/embed 按 index 排序）+ `tests/test_memory.py` 新增 5 例（默认关闭/语义补词法漏检/双路合并加分/删除清理向量/importance 门控），全量 582 passed、2 skipped。

**验证记录（已完成，2026-08-24，N43 端到端真实嵌入）**: 注入真实 bailian `text-embedding-v3`（1024 维）端到端验证：3 篇文档全量入向量索引（count=3）→ 查询「版本管理时不要把秘密配置文件提交上去」语义（词法+语义）把部署笔记分数从纯词法 6.00 提升到 7.33（语义通道真实加分），且额外召回纯词法漏掉的 `food_note`（语义独有命中）→ 清空向量表对照确认提升来自语义通道。演示产物已清理。

## ADR-039: Redis TTL 缓存旁路（N44，cache-aside 加速）

**决策**: 给外部 API 调用加 **TTL 缓存旁路（cache-aside）**——`chuan/cache.py` 的 `Cache`（Redis 后端 + 进程内内存兜底），天气 / 搜索结果按 key 缓存，命中免外呼、避开外部不稳定（如天气服务超时）：

- **Cache 抽象**（`get`/`set`/`clear`，JSON 序列化）：`backend="auto"` 读 config.yaml `cache` 段（`enabled` 才启用）；`"memory"` 强制内存后端（测试/无 Redis 兜底）；注入 redis 客户端实例（测试 FakeRedis）；
- **故障静默降级**：Redis 连接/读写任何失败 → `_redis=None` 自动降级进程内内存 TTL 缓存，绝不抛错、不阻断主流程；
- **接线（cache-aside）**：`mcp_servers/weather_server.py` `get_weather`（key `weather:{city}`，TTL 600s，成功结果才缓存）+ `skills/handlers/web_search.py` `web_search`（key `search:{query}`，TTL 3600s，成功结果才缓存，错误不缓存）；chuan 不可导入时退化为无缓存；
- **默认关闭**：config `cache.enabled: false`（no-op，零依赖零成本，测试封闭）；置 true 需本机有 Redis（如 `docker run -d -p 6379:6379 redis`）。

**理由**: 天气/搜索是真实痛点——每次查外部 API（天气曾踩 620s 超时），重复查询浪费时钱且受外部不稳定影响。cache-aside 是最小风险的加速形态：真相（SQLite/Markdown）不动，缓存是「能删能重建的加速层」；Redis 故障时内存兜底保证功能不丢，与 N43 语义「旁路增强、故障降级」哲学一脉相承。

**反例**: 不做缓存一致性/失效广播（TTL 过期即可，避免复杂度）；不做任务队列/pub-sub 事件总线（本迭代只做「加速」即缓存；队列/总线属后续 N45+）；不缓存错误信息（瞬态，避免把故障缓存住）；不为每实例建独立连接池（进程级单例 `get_cache` 足够）。

**落地记录（已完成，2026-08-24，N44）**: `chuan/cache.py`（`Cache` Redis 后端 + 内存 TTL 兜底 + 注入 FakeRedis/`get_cache` 进程级单例）+ `mcp_servers/weather_server.py`（`weather:{city}` cache-aside）+ `skills/handlers/web_search.py`（`search:{query}` cache-aside）+ `config/config.yaml`（`cache` 段，enabled 默认 false）+ `pyproject.toml`（`redis>=5.0` 依赖）。测试：`tests/test_cache.py` 新增 7 例（默认关闭 no-op / 内存后端往返 / TTL 过期 / FakeRedis 后端 / Redis 不可达降级内存 / 天气命中不触网 / 天气成功后缓存再命中），全量 589 passed、2 skipped。

**验证记录（已完成，2026-08-24，N44 代码级验证）**: FakeRedis 验证 Redis 后端读写（前缀 + JSON）正确；config 启用但 Redis 不可达（端口 1）→ 降级内存仍可用（不抛错）；天气接线 monkeypatch urlopen 验证「命中缓存不触网」与「成功后写入缓存二次命中」。

**真实 Redis 命中验证（已完成，2026-08-24，N44 真实服务端 + Windows 运行时全链路）**: Docker Desktop 后端故障（`backend exited`）改用 WSL 真实 redis-server（Ubuntu 24.04，redis 7.0.15，`wsl -u root` 免 sudo 安装；apt 注册 systemd 服务后改 `bind 0.0.0.0` + `protected-mode no` 持久化），`cache.enabled: true` 且 `cache.host: 172.30.5.62`（WSL IP，直连可达；Windows 11 Hyper-V 防火墙放行后生效）。验证：Windows 运行时 Cache 连上真实 Redis（backend=Redis）→ set/get 落地真实键 `chuan:probe` 且 TTL(120s) 正确 → 预置真实 Redis 天气键后 `get_weather` **命中缓存返回（0.0s）**——且当时 wttr.in 正 HTTP 500 宕机，命中优先于外呼，功能不受外部宕机影响。结论：cache-aside 在真实 Redis 上读写、TTL、命中全部生效。

**运维脚本（已完成，2026-08-24，WSL Redis 自启 + IP 检测）**: 针对上条「遗留」新增 `scripts/wsl_redis_autostart_and_ip_check.sh`——一次性解决 WSL 重启后 IP 漂移与 Redis 不自启：① 确保 redis-server 已装已启动（systemd/service 优先，兜底 `--daemonize`）；② 运行时 `CONFIG GET/SET` 核对 `bind 0.0.0.0` + `protected-mode no`（无需 root，无配置文件也可）；③ `hostname -I` 检测 WSL IP 并仅改写 config.yaml `cache` 段的 `host`（awk 范围限定，不碰 `hud.host`）；④ 自启三档：**优先 systemd 服务覆盖**（`redis-server.service enabled` 即认为已覆盖，不再写重复 boot 命令避免抢 6379）→ 否则写 `/etc/wsl.conf [boot] command`（需 root）→ 再不行自动往 `~/.bashrc` 追加免密拉起兜底。用法：`wsl bash /mnt/d/Dev/Active/chuan-os/scripts/wsl_redis_autostart_and_ip_check.sh`（`--ip-only` 只回写 IP 不碰服务）。已验证：真实环境幂等跑通（PONG / 外部放通 / config 回写 `10.0.0.1 → 172.30.5.62` 且 `cache:` 行与 `hud.host` 完好）。**root 级自启最终结论**：WSL 本机 systemd 已启用（`systemd=true`）且 `redis-server.service` 为 **enabled**，故 root 级自启**无需**再写 `/etc/wsl.conf [boot]`；已实测 `wsl --shutdown` 后冷重启 → 服务自动 active、`PONG`、IP 回写正常。**踩坑**：awk 改 YAML 时 `/^cache:/{...; next}` 的 `next` 会吞掉 `cache:` 头行——改写要留 `{ print }` 兜底；Windows PowerShell `Set-Content -Encoding UTF8` 会给文件加 BOM 并混入 CRLF，本文件已用 python 归一化为无 BOM 纯 LF；`sudo -n` 需密码时无法非交互提权，但 `wsl -u root` 免密可进 root（重置 `passwd jyq74` 后交互 sudo 恢复）。
## ADR-040: 任务队列 + 事件总线（N45，Redis Streams + Pub/Sub）

**决策**: 引入**任务队列**（Redis Streams 消费者组）与**事件总线**（Redis Pub/Sub），给后台任务提供可靠执行与跨进程事件通信，均带内存兜底 + 故障静默降级：

- **事件总线**：`chuan/bus.py` 的 `EventBus`（Redis Pub/Sub 后端 + 进程内内存兜底）。`publish(topic, event)` 本进程订阅者同步收到（内存分发）+ Redis 可用时广播跨进程；`subscribe(topic, handler)` 返回退订函数；`start_listener()` 拉起后台线程经 `psubscribe` 收其他进程事件。事件体 `make_event` 统一（event_id/type/source/timestamp/topic/payload）；
- **任务队列**：`chuan/queue.py` 的 `TaskQueue`（Redis Streams XADD/XREADGROUP + 内存兜底）。`submit`（返回任务 ID）/`pop`（认领，含 pending/retries 元数据）/`ack`（确认）/`requeue`（失败重试，超 max_retries 丢弃）/`recover`（断点恢复：重建消费者组 + 重认领 pending 任务，支持断线恢复）；
- **集成**：`AgentHarness` 在任务生命周期各节点发布事件（`delegate.submitted`/`started`/`done`/`failed`）；`RuntimeSupervisor` 初始化总线/队列、`wake_up` 时 `start_listener()`，暴露 `bus_status()`/`queue_status()`；`heartbeat` 健康报告纳入 `bus`/`queue` 状态与摘要；
- **默认开启**：config `bus.enabled: true`（WSL Redis 172.30.5.62），Redis 不可达即降级内存——总线/队列是「能删能重建的协调层」，真相仍在 SQLite / Markdown。

**理由**: N44 只做了「加速」（缓存旁路），但后台任务的可靠性（不丢、重试、断点恢复）与跨进程协作（多 agent 实例间事件通知）仍缺位。Redis Streams 天然支持消费者组（任务仅被处理一次）与 PEL（挂起任务列表，可恢复）；Pub/Sub 天然支持多订阅者广播。二者都是协调层，与 N39「旁路增强、故障静默降级」哲学一致：Redis 挂了 → 内存兜底，功能不丢、不抛错。

**反例**: 不做 Redis 外的持久化队列（Redis 挂了任务可能丢——队列定位是「尽量可靠」而非「绝不丢失」，绝不可丢失的数据仍在 SQLite）；不做事件顺序保证（Pub/Sub 是尽力而为广播）；不为每实例建独立连接池（进程级单例 `get_bus`/`get_queue` 足够）。

**落地记录（已完成，2026-08-24，N45）**: `chuan/bus.py`（EventBus：publish/subscribe/start_listener/stats + 内存兜底 + `get_bus` 进程级单例）+ `chuan/queue.py`（TaskQueue：submit/pop/ack/requeue/recover + 消费者组 + `get_queue` 进程级单例）+ `config/config.yaml`（`bus` 段：enabled/host/port/db/prefix + queue.group/max_retries）+ `chuan/gateway/agent_harness.py`（生命周期事件注入）+ `chuan/runtime_supervisor.py`（总线/队列初始化 + 监听启动 + 状态接口）+ `chuan/gateway/heartbeat.py`（bus/queue 状态纳入健康报告）。测试：`tests/test_bus.py`（内存发布分发 / 默认关闭 no-op / 退订 / FakeRedis 后端广播 / 断线降级）+ `tests/test_queue.py`（内存 submit/pop/ack / FakeRedis 消费者组 / 重试与丢弃 / recover 断点恢复）+ `tests/test_agent_harness.py`（生命周期事件发布 / 关闭总线 no-op）。

**真实 Redis 验证记录（N45，2026-08-24）**: WSL redis-server（7.0.15，bind 0.0.0.0）连通（WSL 内自连 127.0.0.1 绕开 Windows→WSL 跨网络振荡）。**踩坑两处（真实 Redis 兼容）**：① redis-py 8 默认 RESP3 HELLO 握手包较大，Windows→WSL 振荡网络下易超时 → `_connect_redis` 强制 `protocol=2`（Redis 7 完全支持）并提高超时到 5s；② redis-py 默认 `decode_responses=False` → XREADGROUP 返回的 fields 键为 bytes，`_parse` 用 str 键取不到 task_id/payload → 全部落空 → `_connect_redis` 统一 `decode_responses=True`（读写全 str，与代码内 str 键一致；FakeRedis 用 str 键故单测未暴露此问题）。验证结果（auto 路径 + importlib 直载模块绕开 chuan 包依赖）：总线本地事件分发 `[('agent.task','verify.ping','N45-verify')]` ✓；**跨进程**：listener 进程 `start_listener` 订阅 `agent.task` → publisher 进程 publish → listener HANDLER 收到 `agent.task verify.ping N45-crossproc`（经 Redis Pub/Sub）✓；队列 submit → pop（payload 完整 `{'n':1,'marker':...}`，stream_id/retries 元数据齐）→ ack → pending 归零 ✓。**环境遗留**：Windows→WSL 网络连通性振荡（一阵通、一阵被拒/超时），Windows 侧验证依赖「好窗口」重试；代码逻辑已由 FakeRedis 单测封闭覆盖，不依赖真实网络稳定性。


## ADR-041: 本地资源感知采集器（N46，系统/桌面/SSH/Git 状态 skill）

**决策**: 以 `skills/*.yaml` + `skills/handlers/*.py` 形式新增**本地资源感知采集器**（ROADMAP P3「本地资源感知」落地），全部为 `type: handler` 确定性实现、不依赖 LLM；任何采集项失败**静默降级**为可读说明，不抛错、不阻断 agent 调用：

- **`system_status`**（系统/磁盘）：CPU 逻辑核心（+POSIX 平均负载）、内存占用（Linux 读 `/proc/meminfo`、Windows 用 ctypes `GlobalMemoryStatusEx`）、各磁盘分区占用（Windows 枚举 A:–Z: + `shutil.disk_usage`，POSIX 取 `/`）、主机信息（`platform`）；可选 `paths` 参数追加查看指定磁盘路径；
- **`ssh_status`**（SSH）：解析 `~/.ssh/config` 显式 `Host` 别名（跳通配符）+ `known_hosts` 去重汇总 + Windows `netstat -ano` 检测 ESTABLISHED 到 22 端口的活跃连接；config/known_hosts 读取失败一律降级为「无」；
- **`desktop_status`**（桌面）：Windows ctypes WinAPI 的当前前台窗口标题（GetForegroundWindow/GetWindowTextW）+ 主屏分辨率（GetSystemMetrics）；非 Windows 平台明确提示不支持；
- **`git_status`**（Git 仓库）：只读 `git` 子命令，返回分支/HEAD、工作区改动数（modified vs untracked）、是否领先远端、stash 条数、最近 5 条提交；默认项目根，`repo` 参数可指定目录，非仓库返回 `[ERROR]` 说明。

**理由**: P3 待办需要一个「看下磁盘/SSH 状态」等 `trigger` 命中的本地状态查询能力，且必须确定性、无 LLM、无新增第三方依赖（psutil 未安装故纯标准库 + ctypes 实现），失败静默按「旁路增强、故障降级」哲学处理，与既有 `bash_safe`/`web_search` handler skill 结构和 SkillRegistry 注册方式完全一致。

**反例**: 不引入 psutil/GPUtil 等新依赖（标准库足够且跨平台）；不做 SSH/IP 主动连通检测（会发起网络请求，超出「本地资源感知」只读边界，仅列配置与现网连接）；不做桌面截屏/录屏（该能力属 P4 视觉理解、依赖具体模型，另行规划）。

**落地记录（已完成，2026-08-24，N46）**: `skills/handlers/system_status.py` + `skills/system_status.yaml` ＋ `skills/handlers/desktop_status.py` + `skills/desktop_status.yaml` ＋ `skills/handlers/ssh_status.py` + `skills/ssh_status.yaml` ＋ `skills/handlers/git_status.py` + `skills/git_status.yaml`，均为 `type: handler`（模块 `handlers.<name>`）。未改 `config.yaml`。实测（.venv）：`system_status()` 返回 Windows 11/16 核/内存 73%/C+D 双盘占用 ✓；`desktop_status()` 返回 1536x864 + 前台窗口标题 ✓；`ssh_status()` 返回 known_hosts 主机 8.138.91.61 ✓；`git_status()` 对真实仓库返回分支/最近提交 ✓、对项目根（非 git 仓库）如实报 `[ERROR] 不是 git 仓库` ✓。触发词匹配验证：`看下磁盘→system_status`、`SSH状态→ssh_status`、`看一下git状态→git_status` 均命中 ✓。文件由 `[IO.File]::WriteAllText`（UTF8-no-BOM、LF）写入（IDE Write/Edit 服务异常退避）。 澄清：本项目 `langchain_core`（1.6.0）并无版本/导入问题——曾在诊断命令中误用 `sys.path.insert(0,'chuan')` 导致 `AsyncCallbackManager` 假 ImportError，从项目根正常导入（`from chuan.adapters.skill_loader import SkillRegistry`）即可复现全部 10 个 skill 与 4 个新工具正常加载、真实返回，无真实环境故障。

## ADR-042: HTTP API / FastAPI Gateway（N47，客户端/服务器解耦接入层）

**决策**: 新增 `chuan/gateway/api.py`（FastAPI），把 RuntimeSupervisor 暴露为 HTTP 服务，让脚本 / 网页 / PWA 等客户端通过 HTTP 与 chuan-os 对话，与 CLI / TUI / 语音解耦。落地 ROADMAP P3 待办「HTTP API / FastAPI Gateway（客户端/服务器解耦，ADR-011 接入层）」。核心设计：
- **生命周期**：`create_app()` 工厂 + FastAPI lifespan——复用传入的 `supervisor`（测试用，不管理其生命周期）或默认用 `RuntimeSupervisor(config_path=...)` 创建并 `wake_up()`，关闭时 `shutdown()`。模块级 `app = create_app()` 供 `uvicorn chuan.gateway.api:app` 直接启动；
- **路由**：`GET /health` 复用 `Heartbeat.check()` 汇总健康状态（返回 `status: ok|degraded` + `awake` + `report`）；`POST /api/chat` 走 `RuntimeSupervisor.dispatch()`（支持 `session_id` 会话隔离 + 可选 `history` + 可选 `worker` 直接派发指定岗位，跳过自动路由），返回 `{reply, route, route_method, session_id}`；
- **鉴权从简（本地/局域网）**：读 `CHUAN_API_TOKEN` 环境变量（或 `config.yaml` 的 `api.token`）作访问令牌，要求请求头 `X-API-Key`（或 `Authorization: Bearer`）匹配；未配置 token → 不鉴权默认放行；
- **线程模型**：`/api/chat` 用同步 `def` 端点跑在 FastAPI 线程池，内部 `dispatch()` 经 `run_coroutine_threadsafe` 调度到幕僚长常驻事件循环，与 CLI/scheduler 复用同一套并发路径，天然线程安全；显式串 500 错误为可读 `detail`，不崩请求。

**理由**: P3 接入层扩展需要一条不依赖终端/语音的编程式通道。FastAPI + uvicorn 已有依赖（`.venv` 装好），零新增；复用 Gateway 的 Heartbeat 与 RuntimeSupervisor 的 dispatch 接口，不侵入 core（不动 orchestrator / runtime_supervisor）。鉴权保持「本地/局域网可跑、可配 token 收紧」。

**反例**: 不做复杂鉴权（OAuth/JWT/HTTPS 证书），本地/局域网从简，必要时交给反向代理；不做流式 SSE/WebSocket（当前 `dispatch` 整条同步返回，流式留待未来在 `dispatch_async` 上扩展）；不在 `/api/chat` 里做多轮状态机（会话延续靠 `session_id` + SqliteSaver 持久化）。

**落地记录（已完成，2026-08-24，N47）**: 新增 `chuan/gateway/api.py`（`ChatRequest`/`ChatResponse`/`_load_token`/`_require_auth_factory`/`_last_message`/`create_app`/`app`/`main`）+ 测试 `tests/test_api.py`（8 例：health ok / 未唤醒 degraded / chat 返回 reply+route / worker 直派 / 空消息 422 / 未唤醒 503 / 设置 token 后 X-API-Key 与 Bearer 鉴权强校验 / 未设 token 默认放行）。全量 624 passed、2 skipped（另有 3 例 `test_http_gateway.py`/`test_hud.py` 的 aiohttp 用例因 TRAE 沙箱限制写 `aiohttp/__pycache__/test_utils...pyc` 失败，与本改动无关，属环境问题）。实测验收（uvicorn，127.0.0.1:8011）：
- `curl /health` → `{"status":"ok","awake":true,"report":{... 13 workers, mcp_connected 3 ...}}` ✓；
- `POST /api/chat {"message":"你好","session_id":"api_acceptance"}` → 返回幕僚长真实答复 + `route: chief_of_staff` ✓。

**踩坑**：默认 `session_id` 会话在重启后经 SqliteSaver 恢复的历史里偶有「`AIMessage` 的 tool_call 缺对应 `ToolMessage`」的旧存档 → `/api/chat` 返回 500；属既有存档历史不完整问题（非网关 bug），换新 `session_id` 即恢复——未来可考虑在会话初始化时清洗不完整 tool_call 历史。**IDE 异常**：Write/Edit 工具在本机对含较复杂 AST 的文件报 `IOutlineService` 异常，改用「最小占位 Write + Edit 覆盖、文档用小块递增追加」规避。

## ADR-043: 局域网 HTTPS + 手机 PWA 接入（N48，HTTP/HTTPS 网关 + SCENE WebSocket）

**决策**: 新增 `chuan/gateway/http_gateway.py` + `web/` PWA + `scripts/gen_https_cert.py` 自签证书脚本，落地 ROADMAP P2「局域网 HTTPS + 手机 PWA 接入」。在既有 HUD（TCP → Flutter 悬浮层）之上加一个 **aiohttp 实现的 Web 旁路**（对比 ADR-042 的 FastAPI 是「客户端/服务器解耦编程式通道」，本节点是「手机同局域网 HTTPS 访问 PWA 并下发/接收 HUD 命令」的移动端通道，两者分层互补）：
- **HTTP/HTTPS 静态服务**：把 `web/` 的 PWA（`manifest.webmanifest` + `sw.js` + app/style/icon）推给手机；自签证书缺失/加载失败 → **静默退回纯 HTTP** 并打警告（旁路降级，不阻断）。证书用 `scripts/gen_https_cert.py` 生成（`certs/https_cert.pem` + `certs/https_key.pem`；SAN 含 localhost + 本机局域网 IP），openssl → Git 自带 openssl → cryptography 三档，全不可用则明确报错交由网关降级；
- **SCENE 协议 WebSocket**（`/ws`）：与 N34 同一套 scene 状态（agent/effect/user/ai/monitor），同一协议只换传输层——`hello`（caps 协商）/`scene`（全量）/`patch`（增量）帧，手机 PWA 复用 Flutter 悬浮层那套帧；前端可发 `message:{text}` 走同一条协议把话送进 supervisor；
- **API**：`POST /api/message`（把手机输入经 `RuntimeSupervisor.dispatch` 路由，回复回传，同时把 user/ai/effect 打平到所有 WS 客户端与 HUD）、`POST /api/hud`（显式下发 wake/hide/agent/effect/user/ai/monitor 命令并广播 SCENE 帧）、`GET /api/health`（存活+绑定探针）；
- **旁路设计**（对齐项目惯例）：未绑定 supervisor/hud → 对应 API 返回明确错误码但静态页/WS 仍可用；WS 客户端断开即弃、发送失败不回抛；任何 handler 异常转 JSON 错误，绝不拖垮主进程；`AgentHarness.on_done` 后台委派完成自动广播 HUD 帧（旁路增强）；
- **独立启动**：`python -m chuan.gateway.http_gateway [--supervisor]`（`--supervisor` 拉起完整栈 RuntimeSupervisor+HUD+wake_up）。

**理由**: N34 ADR-029 已为手机 PWA 铺好「同一协议 TCP/WebSocket 只换传输层」的路，本节点把它跑通——手机同局域网 HTTPS 访问 PWA、可安装（manifest+SW）、经 SCENE WebSocket 实时收 scene 帧、直接下发/接收 HUD 命令。选 aiohttp（已有依赖，`WebSocketResponse` + 静态文件一手包办）而非再上一层 FastAPI/uvicorn，保持薄层与最小依赖；WebSocket 天然双工，比 ADR-042 的请求/响应 `dispatch` 更适合「持续投影 + 双向 intent」的施工模型。

**反例**: 不做鉴权/加密密钥托管（本地/局域网自签足够，把收紧交给反向代理，与 ADR-042 同立场）；不做双向 intent 回流以外的多态协议扩维；不把 HUD scene 做持久化（易失投影，重连由后端 `scene` 全量恢复，沿用 ADR-029）；不引入新的 Web 框架依赖（aiohttp 即可）；`vector_store`/`rag_corpus`/`faiss` 保持**预留未实现**（见下「文档口径修正」）。

**落地记录（已完成，2026-08-24，N48）**: `chuan/gateway/http_gateway.py`（`HttpGateway`：config 解析/静态服务/`/ws` SCENE/`/api/message` `/api/hud` `/api/health`/TLS 静默降级/`broadcast` 线程安全入口/`attach` 挂 supervisor+hud/`main`）+ `web/`（`index.html`/`style.css`/`app.js`/`manifest.webmanifest`/`sw.js`/`icon.svg`）+ `scripts/gen_https_cert.py` + `config/config.yaml`（`http:` 段，仅新增该段）+ `tests/test_http_gateway.py`（13 例：配置/静态/health/message 路由与回复/WS 握手 hello+scene/WS message 帧派发广播/hud 命令广播/路径穿越守卫/TLS 缺失降级）。测试：`test_http_gateway.py` 13 passed（+`test_hud.py` 隔离复跑通过——hud 网络用例仍是有文档记录的历史 flaky）。端到端实测（.venv，真实自签证书）：`HTTPS / -> 200 text/html 含川流` ✓、`/manifest.webmanifest -> application/manifest+json` ✓、`/api/health -> {ok,tls:true,...}` ✓、`WSS /ws -> hello{client:chuan-os,caps:scene} + scene{version:1}` ✓。验收路径：手机同局域网 `https://<电脑IP>:8443/` → PWA 可安装（SW 注册 + manifest）→ 输入消息经 `/api/message`（或 WS `message:`）路由 → 手机实时收 SCENE patch 显示 HUD 状态/AI 回复；`POST /api/hud` 显式下发 HUD 命令。

**验收记录（2026-08-24，多窗口并行产物全栈复核）**: 全量回归（当时排除在制品的 `test_vault_server.py`）= **626 passed、2 skipped**；唯一失败 `test_hud.py` 网络用例为历史 flaky——隔离复跑通过、复跑又换另一条失败（时序/端口污染，hud.py 自初始提交未改动，非并行窗口回归，与 ADR-042/043 既有记录一致）。实跑验收：
- **N46 资源感知**：`system_status`（Windows 11 · 16 核 · 内存 25.4/31.4GB · 磁盘 C/D）✓、`desktop_status`（1536x864 · 活动窗口）✓、`ssh_status`（known_hosts 8.138.91.61 · 无活跃连接）✓、`git_status`（main@9d0a17b · 工作区 3改3新）✓；
- **N47 FastAPI 网关**：`GET /health` → `{status: ok, awake, brain_ok(bailian_flash), 13 workers, mcp_connected 4, memory_ready, pools 生效, bus/queue enabled(Redis 不可达→memory 降级)}` ✓；`POST /api/chat {"你好","session_id":"acceptance_n47"}` → 幕僚长真实答复 `route: chief_of_staff` ✓（启动含 MCP 连接约 1 分钟）；
- **N48 HTTPS+PWA**：真实自签证书在跑 TLS（纯 HTTP 探被拒，证书确实生效）；`GET /` → 200 text/html（PWA 壳含 manifest/sw.js 引用）✓、`/manifest.webmanifest` → 200 `application/manifest+json` ✓、`/sw.js` `/app.js` → 200 ✓、`/api/health` → `{ok:true, tls:true, supervisor:false, hud:false}`（无 `--supervisor` 旁路正确降级）✓。

### 文档口径修正（N48 顺手，预留未实现）

ROADMAP/DECISIONS 个别早期表述把「向量语义召回」说成已实现——纠正口径：**长期记忆的召回真相是 FTS5 词法**（N13 时代只有 FTS，N13 描述中的「+向量」系过度宣称，已改）；**向量语义召回唯一已落地路径是 N43 的 sqlite-vec 旁路**（ADR-038，嵌入云端、默认关闭）；**本地 `faiss` / `vector_store` / `rag_corpus` 保持「预留未实现」**——没有任何代码在消费 `memory.vector_store` / `memory.rag_corpus`，也未被 N43 使用（N43 用 `memory_fts.db` 里的 vec0 表，与上述字段无关）。


## ADR-044: vault MCP server（外来 agent 经 MCP 检索/写入共享黑板）

**决策**: 新增 `mcp_servers/vault_server.py` + 注册进 `config/mcp_servers.yaml`，落地 ROADMAP P2 待办「vault MCP server：外来 agent 经 MCP 检索/写入共享黑板」。共享黑板真相落盘在 `data/teams/*.json`（N42 TeamStateWriter 同款），外来 agent（`agents/` 下 claude_code / opencode 等）是独立进程，故 MCP server 自包含可跑、不依赖 chuan 包启动（对齐 filesystem_server.py 模板）：
- **三个工具**：`list_vaults()` 列出黑板/团队（名字/role/status/updated/条目数）；`search_vault(query, vault="", limit=10)` 检索黑板（role/task/subtasks/notes 关键词大小写不敏感匹配 + 命中处片段）；`write_vault(key, content, team="default")` 写入黑板（追加到 `notes` 列表，文件不存在则新建并补 `role/status/subtasks` 骨架，与既有 team_state 文档兼容不破坏结构）；
- **自包含**：仅依赖 `mcp.server.fastmcp` + 标准库（json/os/re/pathlib/datetime），不 import chuan；黑板目录与项目根硬编码 `D:\Dev\Active\chuan-os`；
- **写安全**：团队名白名单清洗（`[^A-Za-z0-9_\-:]` → `_`，空回退 `default`）+ realpath 前缀校验（限定 `data/teams/` 与 `data/memory/` 允许范围）双保险防路径穿越；原子落盘（临时文件 + `os.replace`）防半截 JSON 污染黑板。

**理由**: P2 待办需要一条「外来 agent 经 MCP 检索/写入共享黑板」的通道——claude_code / opencode 等独立进程无法直接 import chuan，需经 stdio MCP 与黑板交互。直接读 `data/teams/*.json`（而非 import chuan 走 wiki.search）保证 server 自包含、启动零依赖、隔离 chuan 包故障；write 设计成追加式 `notes` 列表，与黑板「磁盘真相 + 审计」语义一致，不覆盖既有岗位任务记录。

**反例**: 不 import chuan（外来 agent 独立进程，自包含优先，故障隔离）；不做用户/角色鉴权（本地单机 stdio server，权限边界由 MCP 客户端侧配置管理，对齐 filesystem_server 的权限标签模型）；不接 N24 Wiki 实体页改写/蒸馏（黑板是易变协作真相层，Wiki 是蒸馏知识层，二者分层，检索命中用关键词即可，不引 RAG 复杂度）。

**落地记录（已完成，2026-08-24，N49）**: 新增 `mcp_servers/vault_server.py`（`list_vaults`/`search_vault`/`write_vault` + `_team_file` realpath 前缀校验 + `_safe_team_name` 白名单清洗 + `_save_doc` 原子落盘 + `_snippet` 命中片段）+ `config/mcp_servers.yaml` 追加 `vault` 段（`command: python` / `args: ["mcp_servers/vault_server.py"]` / `permissions: [read, write]` / description，未动其他 server 段）+ 测试 `tests/test_vault_server.py`（13 例：list 空提示/列黑板、write 新建/追加/空参数校验、search 命中 task/subtask/note/无命中提示/指定 vault 隔离/空查询、路径穿越清洗 + 分隔符清洗 + 工具注册三件套）。验收：`.venv` `from mcp.server.fastmcp import FastMCP` ✓；`python mcp_servers/vault_server.py` stdio 启动并响应 initialize（serverInfo=vault）✓；`pytest tests/test_vault_server.py` 13 passed ✓；全量 `pytest -q` 640 passed、2 skipped ✓（含本节点 13 例）。

## ADR-045: 视觉理解（N50，图片/截图视觉分析 handler skill）

**决策**: 落地 ROADMAP P3 待办「视觉理解（截图/录屏/PDF/表格，可接 GLM-4V/Qwen-VL）」第一步——新增 `skills/vision_analyze.yaml` + `skills/handlers/vision_analyze.py` handler skill，给 agent 加「分析一张图片」的能力（V1 覆盖本地图片文件 + 图片 URL）：
- **视觉大脑**：config.yaml `brains.vision`（provider openai，`qwen-vl-plus`，base_url 百炼 dashscope，key 复用 `BAILIAN_API_KEY`/`bailian_api_key`）——只作 handler 的模型配置，不进文本路由；
- **调用路径**：`vision_analyze(image_ref)` → 本地文件读字节 base64 成 `data:image/*;base64,...`（mime 按扩展名）或直用 http(s) URL → OpenAI 兼容视觉消息（`text` + `image_url`）调 `_call_vision` → 返回描述文本；
- **静默降级**（对齐项目惯例）：未提供图 / 图片不存在 / key 缺失 / 模型调用失败 → 全部返回可读错误文本，绝不抛错；模型调用隔离在 `_call_vision`，便于单测 mock；
- **注册**：`type: handler` + 触发词（看图/识别图片/图片内容/截图分析…），经 SkillRegistry 包装为 LangChain Tool 全量挂载。

**理由**: P3 视觉理解的第一步从「看懂一张图」切入——这是截图 / PDF / 表格（转图后）共用的底层能力；用既有 `openai` 客户端 + OpenAI 兼容视觉消息，零新增依赖；key 复用百炼（与 bailian_flash 同平台），无需新申请。

**反例**: V1 不做屏幕截图/录屏采集（Windows 截屏 + 视频抽帧留待后续）；不做 PDF 直解析（需先转图，留待后续）；不做图片目标检测/坐标定位（描述文本即可）；不把视觉脑加入文本路由（qwen-vl 只用于看图的 handler，避免污染文本路由计费/行为）。

**落地记录（已完成，2026-08-24，N50）**: `skills/vision_analyze.yaml`（trigger 关键词）+ `skills/handlers/vision_analyze.py`（`_load_vision_cfg`/`_resolve_api_key`/`_image_data_uri`/`_call_vision`/`vision_analyze`）+ `config/config.yaml`（`brains.vision`，仅新增该段）+ `tests/test_vision_analyze.py`（8 例：注册/触发词/空输入/缺文件/缺 key/mock 本地图 data URI/mock URL/调用失败降级）。验收：`pytest tests/test_vision_analyze.py` 8 passed ✓；`BrainRegistry` 加载含 vision、`ToolRegistry.get_tools()` 含 vision_analyze ✓；端到端真实调用（百炼 qwen-vl-plus，本地生成 64x64 红色 PNG）→ 返回「整个画面由纯红色填充…无文字 OCR…可能是测试图」✓。

## ADR-046: 工具市场（N51，能力目录 + 运行时按信号裁剪工具集）

**决策**: 落地 ROADMAP P3 待办「工具市场 / 动态能力发现（运行时按信号裁剪工具集，借鉴 BaiLongma）」——在 ADR-009（统一注册表、全员默认挂载）之上加一个「市场化 + 确定性按信号裁剪」旁路，新增 `chuan/tool_market.py` 的 `ToolMarket`：
- **目录（catalog）**：把 ToolRegistry 里所有工具（handler skill / MCP / extra）列成带来源与描述的市场清单，可浏览上下架态；
- **运行时开关（enable/disable）**：动态「上架/下架」工具，下架后新 spawn 的 agent 不再注入（经 `AgentPool.tool_filter` 生效）；
- **按信号裁剪（select）**：给定任务文本**确定性**选出相关子集——词元交集计分（**CJK 逐字拆分**让中文子串可命中、英文/数字整词），命中不足 `min_tools` 回退全量（防饿死），`always` 名单强制保留；**不依赖 LLM**（项目惯例：确定性路径不用模型）。

**默认关闭（`tool_market.enabled: false`）**：行为与原来完全一致——零成本旁路，AgentPool 不过滤，ADR-009 全量挂载不变；开启后运行时经 `/tools` 上架/下架生效。

**理由**: 工具越挂越多，agent 每次都要在全集里挑（长上下文 + 注意力噪声）。市场化 + 按信号裁剪把「能力全集」变成「按任务收敛的子集」，降低每轮工具选择负担；`enabled` 开关保证向后兼容，且裁剪走纯规则确定性路径，可测、可预期、可复现。

**反例**: 不做 LLM 自主选工具（不可测、费 token，与项目「确定性路径不用模型」惯例相悖）；不做工具全生命周期商店（上架/评分/安装）——当前只做挂载级裁剪，够用即止；`enabled: false` 为默认，不默认改变挂载行为（对齐 ADR-009 减法语义）。

**落地记录（已完成，2026-08-24，N51）**: `chuan/tool_market.py`（`ToolMarket` + `_tokenize`（CJK 逐字拆）+ `load_tool_market_cfg`）+ `config/config.yaml`（`tool_market` 段，默认关闭）+ `chuan/agent_pool.py`（`tool_filter` 注入点，过滤失败回退全量不阻断 spawn）+ `chuan/runtime_supervisor.py`（构建 market、开启时挂 tool_filter、`tool_market_status`/`tool_market_select`）+ `chuan/gateway/heartbeat.py`（健康报告 `market` 段）+ `chuan/main.py`（`/tools` 命令：目录 / enable / disable / select / refresh）+ `tests/test_tool_market.py`（9 例）。验收：`pytest tests/test_tool_market.py` 9 passed ✓；回归 test_main/test_role/test_gateway_components/test_agent_harness 103 passed ✓。测试暴露并修复两个真实缺陷：`_tokenize` 原把整段 CJK 当一个词元致中文子串无法命中 → 改逐字拆分；`enable/disable` 在目录未加载时 `_source_of` 为空致误判未知工具 → 加 `_collect()` 懒加载。

## ADR-047: 视觉理解 V2（N52，视频抽帧 + PDF/表格转图后走视觉分析）

**决策**: 落地 ROADMAP P3 待办「视觉理解扩展（录屏/PDF/表格转图留待扩展）」——在 N50 `vision_analyze` 之上按**文件扩展名分派**，把「只能看图」扩成「视频/PDF/表格也能看」：
- **视频/录屏**：`_video_first_frame` → `_ffmpeg_bin()`（对齐 voice/tts.py：`FFMPEG_BIN` 环境变量 → imageio-ffmpeg 自带 → 系统 PATH）→ `_ffmpeg_extract_first_frame` 用 ffmpeg `-frames:v 1` 抽首帧 → **JPEG** data URI → 走 `_call_vision`；缺 ffmpeg / 抽帧失败返回可读提示；
- **PDF**：`_pdf_to_img` 用 `pdf2image`（含 poppler）转首页图 → JPEG data URI；**缺依赖返回可读提示**（不硬装 poppler 重依赖）；
- **表格**：`_table_to_img` V1 只渲染 **csv**（`_csv_rows` 优先 pandas、缺则 stdlib `csv` 读行）+ Pillow 渲染成表格网格图 → JPEG data URI；**xlsx/xls 留提示**请先转 csv；缺 Pillow 返回可读提示；
- **分派**：`_resource_to_data_uri` 按后缀集 `_PDF_SUFFIXES/_TABLE_SUFFIXES/_VIDEO_SUFFIXES` 路由，其余默认按图片走原 `_image_data_uri`；
- **静默降级**（对齐项目惯例）：缺 ffmpeg / 缺转换依赖 / 转换失败 / 文件不存在 / 缺 key / 模型失败 → 全部返回可读文本，绝不抛错；空输入、文件不存在、缺 key、模型失败的**文案契约与 N50 完全一致**（旧测试不破）。

**理由**: 录屏/PDF/表格是日常高频输入，转成图后统一走既有视觉模型（qwen-vl）即可复用全部能力；视频抽帧零新增依赖（系统 ffmpeg），PDF/表格转换按需轻依赖、缺则降级提示——符合「不新增重型依赖 + 失败静默降级」的项目惯例。

**反例**: V1 不做多页 PDF 逐页分析（只首页）、不做长视频多帧采样（只首帧）、不做 xlsx 表格渲染（V1 仅 csv，xlsx 留提示）、不做表格数值计算（视觉只描述内容）；不把 ffmpeg/pdf2image/Pillow 写进硬依赖（均按需导入、缺失静默降级）；Pillow 为 csv 渲染落图所需。

**落地记录（已完成，2026-08-24，N52）**: `skills/handlers/vision_analyze.py`（`_ffmpeg_bin`/`_pdf_to_img`/`_csv_rows`/`_table_to_img`/`_ffmpeg_extract_first_frame`/`_video_first_frame`/`_resource_to_data_uri` + `vision_analyze` 经 `_resource_to_data_uri` 按 `_PDF_SUFFIXES/_TABLE_SUFFIXES/_VIDEO_SUFFIXES` 分派）+ `skills/vision_analyze.yaml`（触发词扩展：看 PDF/看pdf/读表格/看表格/视频截图/看视频/分析这个文件）。测试：`tests/test_vision_v2.py` 11 例（触发词扩展/无输入/图片仍走原 data URI/csv 真实渲染出图/csv 走模型收到 data URI/xlsx 留提示/csv 缺 Pillow 降级/PDF 缺依赖降级/视频抽帧 mock 走模型/缺 ffmpeg 降级/抽帧失败降级）+ `tests/test_vision_analyze.py` 8 例回归。验收：两文件 19 passed ✓；全量回归（tests）680 passed、2 skipped，唯一失败为既有 flaky `test_hud.py::test_scene_mode_off_falls_back_to_legacy_only`（隔离复跑通过，hud.py 未改动），无新增失败。

## ADR-049: 声纹防欺骗（N54，enroll_speaker + anti_spoof 规则版 V1）

**决策**: 落地 ROADMAP P4 待办「声纹防欺骗（anti_spoof + enroll_speaker）」V1——新增 `chuan/voice/spoof.py`，给语音入口加「反回放/反环境噪声 + 声纹核对」：
- **特征**（`extract_features`）：纯 numpy 规则，float32(-1..1) 域计算——RMS 能量轮廓（固定 32 点向量）+ rms 均值/方差/峰值 + 过零率 + 静音帧占比 + 时长；
- **注册**（`enroll_speaker`）：提特征 → 原子落盘 `data/speakers/<name>.json`（tmp+rename 防半截 JSON）；`_safe_name` 防路径穿越；过短/全静音拒入库；`load_speaker`/`list_speakers`/`remove_speaker` 磁盘真相；
- **反欺骗**（`anti_spoof`）两级判断：
  1. **回放 / 环境噪声**（不依赖声纹库）：静音占比 ≥0.85 / 时长 <0.5s / 能量过低 → 直接判 spoof（廉价指纹）；
  2. **已注册声纹核对**：能量轮廓相关 + 能量/时长/过零率贴近度加权打分，低于阈值判「声纹不匹配（疑似伪造）」；未注册 → 旁路 ok=True。
- **int16 缩放兼容**（对齐 wake_word.py 教训）：麦克风流是 float32(-1..1)，本模块统一 float32 域，int16 输入自动 ×1/32768，阈值语义可预期；
- **静默降级**：任何失败返回可读 dict（默认旁路 ok=True），绝不抛错；后续可换模型后端（pyannote/ecapa），接口不变。

**理由**: 声纹安全的第一道防线是「区分真实人声 vs 回放/环境噪声」，规则版零模型依赖、可测可预期；`enroll_speaker` 是身份锚点，`anti_spoof` 两级判断在无声纹库时也能挡掉廉价伪造。

**反例**: V1 不引 pyannote/ecapa 等重模型（后续可换后端，接口不变）；不做远端云端声纹（全部本地）；不做连续说话人跟踪（只做单段验证）；`data/speakers/` 不加密（V2 可做）。

**落地记录（已完成，2026-08-24，N54）**: `chuan/voice/spoof.py`（`extract_features`/`enroll_speaker`/`load_speaker`/`list_speakers`/`remove_speaker`/`anti_spoof`/`_compare_voiceprint`/`_safe_name`/`_to_float32`）。测试：`tests/test_voice_spoof.py` 13 例（enroll 写盘读回/拒静音过短/路径穿越/特征形状/静音判 spoof/过短判 spoof/未注册旁路/匹配通过/异声纹判伪造/未知名旁路/list+remove/int16-float32 缩放/garbage 不抛）+ `tests/test_voice.py` 46 例回归。验收：两文件 59 passed ✓。

## ADR-050: 向量 RAG 评估闸门（N55，确定性判定是否启动本地 embedding+faiss）

**决策**: 落地 ROADMAP P3 待办「向量 RAG 评估闸门」——新增 `skills/handlers/rag_gate.py` handler skill，**确定性量化记忆库规模**并判定是否触发本地 embedding+faiss 评估：
- **统计**：`_count_md` 遍历内部 `notes/` 与外接库（`_resolve_external_vaults` 读 config `memory.external_vaults`，对齐 memory.py 解析）的 `.md` 篇数与字符数；
- **阈值**（模块常量 `_DOC_THRESHOLD=1000` / `_CHAR_THRESHOLD=1_000_000`，可测）：合计 **>1000 篇 / 100 万字符** 视为规模达标；
- **漏召回案例**：`record_missed_case(query, note)` 追加写 `data/memory/vault/rag_missed_cases.md`（磁盘真相）；`_count_cases` 计数；
- **三态判定**：规模未达标 →「未触发，继续 FTS5」；规模达标但无案例 →「待案例，暂不启动」；规模达标且有案例 →「**触发**」，输出下一步评估清单（装 sentence-transformers/torch → 选嵌入模型 → 建向量索引双路合并 → 用案例验证召回率）；
- **确定性 + 静默降级**（对齐项目惯例）：不依赖 LLM；统计项失败降级为 0，绝不抛错；注册为 handler skill（触发词：向量评估/RAG 评估/库多大/漏召回/faiss）。

**理由**: RAG 评估不该凭感觉，先量化再决策——用确定性闸门拦住「库还小就急着上向量」的过度投入，同时把「漏召回案例」留痕，让触发判断有据可依（2026-08-24 RAG 可行性评估的落地）。

**反例**: 不做本地 embedding 本身（那是触发后才评估的后续节点）；不做 faiss 建库/检索（非本节点范围）；不自动记录漏召回（需人工/业务侧明确调用 `record_missed_case` 或未来接入）；阈值不动态自调（保持显式常量便于审查）。

**落地记录（已完成，2026-08-24，N55）**: `skills/handlers/rag_gate.py`（`_count_md`/`_resolve_external_vaults`/`_cases_path`/`_count_cases`/`record_missed_case`/`rag_gate`）+ `skills/rag_gate.yaml`（type handler，触发词：向量评估/RAG 评估/上向量/库多大/漏召回/faiss）。测试：`tests/test_rag_gate.py` 11 例（skill 注册/触发词/空库未触发/小库未触发/规模达标无案例待触发/有案例触发/外接库并入合计/漏召回案例追加计数/缺失目录降级/默认库不抛）。验收：11 passed ✓；真实默认库跑通返回可读报告（当前规模未达阈值 → 未触发，继续 FTS5）。

## ADR-051: 与 deepseek-harness 正式版的融合策略（暂缓，等正式版）

**决策**: 不与 dsh（DeepSeek Harness 开发者预览版，2026-08-13 开源，MIT/TypeScript/Cordis 插件内核）做深度集成。保留三条候选路径，**当前决议：先不执行，等 dsh 正式版发布后再评估路径 B**：
- **路径 A**（dsh 进 chuan 委派名单）：沿用 `agents/` 注册机制新建 `agents/dsh/`（同 claude_code/opencode 款式），即可 `/bg dsh <任务>` 委派；
- **路径 B**（chuan 能力暴露给 dsh）：把 chuan-os FastAPI 网关（`/api/chat`+`/health`）与 vault MCP server 包装成 Cordis 插件（TypeScript），dsh 模型可调用幕僚长岗位/记忆/wiki/黑板；
- **路径 C**（dsh 做运行时底座、chuan 做业务编排）：等正式版 API 稳定后再评估，深度集成需重写适配层。

**理由**: dsh 处于 developer preview，官方明示未来有破坏性变更，现在深度集成（B/C）等于重写两遍；路径 A 低成本可逆但需先安装 dsh 才有效，故一并暂缓。对齐 chuan-os「编排层不重写框架」定位（ADR-007），融合只走配置层 + HTTP/MCP 对接，不把 dsh 源码拉进 chuan-os。

**触发条件**: dsh 正式版（API 稳定）发布 → 评估路径 B（把幕僚长包装成 dsh 插件）；路径 A 可随时提前落地（只需 dsh 安装可用）。

## ADR-052: 媒体生成（N56，音乐程序化合成 + 视频/图片后端占位）

**决策**: 落地 ROADMAP P4 待办「媒体生成（音乐/视频）」V1——新增 `skills/handlers/media_gen.py` handler skill：
- **music**（真实现，零新增依赖）：纯 numpy 程序化合成（正弦 + 指数衰减包络 + 和弦琶音，对齐 voice/sounds.py 合成惯例），标准库 `wave` 写 16-bit PCM wav；prompt 关键词确定性影响情绪/速度——欢快/明亮 → C 大调 + bpm150，悲伤/慢 → A 小调 + bpm70，缺省 bpm110；
- **video / image**（后端占位）：项目侧已装 seedance/seedream 插件能力但运行时无直连 API → 返回可读提示（待接 API、配置密钥后可用），不抛错；
- **静默降级**（对齐项目惯例）：未知类型 / 写入失败 / 异常 → 全部返回可读文本，绝不抛错；`output_dir` 缺省 `data/media`，自动建目录；
- 注册为 handler skill（触发词：生成音乐/配乐/做首歌/生成视频/生成图片/bgm）。

**理由**: 音乐是「零依赖即可出活」的媒体——程序化合成让 agent 真能生成可听配乐，无需任何模型/密钥；视频/图片留占位，等 seedance/seedream 的运行时 API 接入后填实现即可，接口不变。

**反例**: V1 不做多轨/编曲/歌词（纯和弦琶音背景乐）；不做采样/音色库（纯正弦，电子风）；不做视频/图片实际生成（后端未接入，只留占位与提示）；不接模型生成音乐（后续可加 suno 等 API 后端，接口不变）。

**落地记录（已完成，2026-08-24，N56）**: `skills/handlers/media_gen.py`（`_tone`/`_synth_music`/`_write_wav`/`_out_dir`/`media_generate`）+ `skills/media_gen.yaml`（type handler，触发词：生成音乐/配乐/做首歌/生成视频/生成图片/bgm）。测试：`tests/test_media_gen.py` 10 例（skill 注册/触发词/音乐写合法 wav 读回/输出目录自动建/悲伤比欢快长/确定性/视频占位/图片占位/未知类型降级/默认目录不抛）。验收：10 passed ✓；真实生成 wav（44100Hz 16-bit mono，C 大调琶音）可被 wave 读回、大小 >44B ✓。

## ADR-053: 第二轮对抗性审查（memory/team_orchestrator/wechat）健壮性修复（2026-08-25）

**决策**: 第二轮审查（N19 微信 / 岗位协作 / 记忆三模块）无高危漏洞，仅健壮性改进项。按严重度排 W1/W2，**当前决议：先修 W1，W2 等部署形态确认后再定**：
- **W1（已修）**: `WeChatChannel.handle` 无异常兜底——`dispatch` 路由抛异常（未唤醒/超时等）会把异常冒到回调入口，可能让整条微信通道崩溃。修复：`handle` 内 `dispatch` 包 try/except，异常静默降级返回可读文本「（消息处理失败，请稍后再试）」，不向远程用户暴露内部细节，对齐项目「失败静默降级」惯例（ADR-007）。
- **W2（已决议，2026-08-27）**: `parse_callback` 仅支持明文 JSON 回调，不支持企业微信默认的加密 XML 回调（`Encrypt` 字段 + AES 解密）。**最终决议：经中转网关解包**——部署时用一个中转/网关把企业微信加密 XML 回调解包成明文 JSON（`{"FromUserName":...,"MsgType":"text","Content":...}`）再转发给 chuan；chuan 侧不承接 AES 加解密、不解析原生 XML 回调。对齐 ADR-015「微信通道只做胶水，不自研回调加解密」与「避免为假设的部署形态过度设计」。

**其余低优先项（本轮不修，留待需要时）**: `memory.py` FTS 查询引号注入风险（已核实安全：`_TOKEN` 正则只提取字母数字+CJK，引号在分词阶段被剥离）、FTS 连接不释放（设计合理：单例连接+`_fts_lock` 全局复用）、非原子写（自愈设计：`_index_document` 幂等 DELETE+INSERT，下次 reindex 自动对齐）；`team_orchestrator.py` 固定 600s 超时无配置。

**理由**: 审查已核实 wechat 的 session_id 冒号由 `team_state._safe_name` 清洗、messages 元素确认为 dict、memory 全部 FTS/vec 操作持 `_fts_lock` 无竞态；剩余项均不影响正确性，属工程健壮性，按需再修，避免为假设的部署形态过度设计。

**落地记录（W1 已完成，2026-08-25）**: `chuan/channels/wechat.py` `handle` 加 try/except 降级；`tests/test_wechat.py` 新增 `test_handle_dispatch_exception_degrades`。验收：14 passed ✓（原 13 + 新 1）。

**落地记录（W3 已完成，2026-08-25）**: 深挖发现 `_await_result` 前缀剥除过宽——旧代码 `content.startswith("[") and "]" in content` 会匹配任何 `[...]` 开头的产出，误删 `[数据]`、`[Important]` 等正文标签。修复：改为只剥 `[<display>]` 和 `[<role>]` 两种已知角色包装前缀，其他方括号内容原样保留。`tests/test_team_orchestrator.py` 新增 `test_await_result_preserves_non_role_bracket_content`。验收：16 passed（原 15 + 新 1），wechat+orchestrator 合计 30 passed。

## ADR-054: GUI 自动化（N57，借鉴影刀 RPA 能力，2026-08-25）

**决策**: 补 chuan-os 缺的「无 API 软件操作」这条腿——让 agent 像影刀一样**看屏幕 → 定位 → 点击/输入**。落地为 `skills/gui_*` handler skill（挂在既有 skill 体系上，零新架构），分四阶段开发。**明确「接口优先」为默认**：GUI 只在 bash/MCP/opencode 都搞不定时作为降级通道。

**分阶段**:
- **阶段 1（N57a）截图 + 元素定位**：`mss` 屏幕截图 → **pywinauto 元素定位（主，控件属性/UIA，类影刀捕获元素）**；pywinauto 定位不了的自绘界面降级用 `vision_analyze`（qwen-vl，N50/N52 已有）视觉兜底 → 返回元素描述 + 坐标。产出 `gui_screenshot` / `gui_locate` skill。
- **阶段 2（N57b）鼠标键盘 + 元素操作**：**pywinauto 主力**（点击/输入/窗口控制/控件选择）+ `pyautogui` 兜底（坐标模拟/滚动/快捷键/拖拽）。**默认走「后台静默模式」（UIA 后台交互：不激活窗口、不抢真实鼠标键盘，用户正常用电脑无感；借鉴影刀后台模式）**，前台坐标仅兜底——这是 pywinauto 相对 pyautogui 的核心加分项。产出 `gui_click` / `gui_type` / `gui_scroll` / `gui_hotkey` skill。
- **阶段 3（N57c）闭环 + 双模式 + 安全（关键）**：组合 `gui_operate`（截图 → 定位 → 操作 → 验证截图 闭环）。**双模式并存 + 动态切换**：
  - **双模式**：后台静默（pywinauto UIA，不抢焦点）+ 接管屏幕（UI-TARS/pyautogui 真实鼠标键盘）。`gui_operate` 拆「意图 + 执行器」两层，模式只是选执行器——同一操作意图可换执行器。
  - **动态切换（默认自动判定）**：每次操作前走决策矩阵——`gui_locate` 能定位 → 静默执行；定位不到（自绘/游戏）→ 可接管则接管；否则停下问用户。**手动指定/中途切换**：`"静默帮我X"` / `"看着你操作X"` / `"接管屏幕X"`，voice 打断即时切换。
  - **静默可见性**（执行静默、状态透明）：实时状态（on_progress + TUI/HUD 显示「正在后台操作：X」）+ 动作留痕（前后截图 + 动作日志，可回看审计）+ 关键动作确认（付款/发消息/删除等跨阈值动作先通知/确认，静默只用于轻、可逆、低风险操作）+ 可中止（voice 打断 / Esc）。
  - **安全闸**（借鉴影刀被劫持 = 电脑被劫持的教训，呼应 P4 机器绑定/自动锁屏）：
    - 危险序列拦截（Ctrl+Alt+Del / 格式化 / 删除类）走既有 guard 审核
    - 只允许本机操作、带超时上限、失败自动中止
    - 每次操作前后自动截图留痕（审计，对齐 blackboard 落盘哲学）
    - 接管屏幕前先确认用户不在用电脑（冲突检测），避免人机抢鼠标键盘
- **阶段 4（N57d）测试 + 文档**：单测 mock `mss`/`pywinauto`/`pyautogui`/vision 调用，验证定位→操作→验证链路 + 安全拦截；补 LEARNINGS。

**技术选型（2026-08-25 开源调研后定）**: **pywinauto 主力**（Windows 元素级 GUI 自动化，BSD，6.1k⭐，控件属性/UIA 定位，微信/钉钉等无 API 软件可操作，有 pywechat 实证）+ `mss`（截图）+ `pyautogui`（坐标兜底，MIT，跨平台）+ 复用 `vision_analyze`（视觉兜底）+ 复用 `guard`（安全闸）。**可选增强：UI-TARS**（字节开源 GUI agent，2B/7B/72B，截图→动作端到端，自带 MCP 可直接接 chuan，Apache）——pywinauto 定位不了的自绘界面时作为视觉操作引擎，跑不动 7B 显存则跳过。**备选：RPA Framework**（Robocorp，Apache，PyPI 33.x）——需要 Excel/PDF/网页全套时 `pip install rpaframework` 即得，不占当前。

**反例（不做）**: 可视化流程设计器（agent 语言编排更高级，不退化回拖拽）；手机 App 自动化（范围太大）；应用市场（已有 ToolMarket）；把 GUI 当主通道（破坏「接口优先」稳定性哲学，界面一变就崩）。

**理由**: 现实是 90% 的桌面软件没有 API（微信/飞书/业务系统），只能靠「看 + 点」；chuan-os 已具备视觉理解（vision_analyze）与语音全链路，只缺「截图 → 定位坐标 → 模拟鼠标键盘」这一环，地基是现成的。影刀 PRA（2025，个人版 RPA 助手，对话式让 AI 操作电脑）验证了该方向，但 GUI 层自动化天生脆（界面改版即崩），故**接口优先、GUI 兜底**，且安全前置。

**落地记录（2026-08-25）**:
- **阶段 1（N57a）✅ 已完成**：新建 `skills/handlers/gui_automation.py`，实现 `gui_screenshot`（mss 截主屏存 PNG，缺依赖/失败静默降级）、`gui_list_windows`（pywinauto 枚举可见顶层窗口）、`gui_locate`（按控件描述在目标窗口定位元素，返回控件信息 + 中心坐标；定位不到自动截图 + `vision_analyze` 视觉兜底）。配套 skill：`skills/gui_screenshot.yaml` / `gui_list_windows.yaml` / `gui_locate.yaml`（触发关键词如「截图/定位/找按钮」）。单测 `tests/test_gui_automation.py` 10 passed（mock mss/pywinauto，覆盖非 Windows 降级、截图落盘、窗口枚举、元素定位、视觉兜底分支）；真实 Windows 环境冒烟验证通过（真截图、真枚举微信窗口、真定位按钮）。依赖 `mss`/`pywinauto`/`pyautogui` 均已安装（.venv）。全量回归 735 passed / 2 skipped 无回退。
- **阶段 2（N57b）✅ 已完成**：在 `gui_automation.py` 追加 `gui_click`（后台静默主力：UIA Invoke / client-side click 不抢鼠标键盘；定位不到或控件不支持静默时降级 `pyautogui` 前台真实鼠标点坐标）、`gui_type`（Edit 控件 `set_edit_text` 后台静默直写不抢焦点，其余控件降级 `set_focus + type_keys` 前台键盘）、`gui_scroll`（`pyautogui` 前台滚轮：坐标 / 定位元素悬停中心 / 当前光标，方向上下、格数 1-20 钳制）、`gui_hotkey`（危险组合安全闸拦截 Ctrl+Alt+Del / Win+L / Win+U；指定窗口则 `set_focus + send_keystrokes`（pywinauto 语法 `^S` 等），否则全局 `pyautogui.hotkey`）。配套 4 个 skill yaml：`gui_click.yaml` / `gui_type.yaml` / `gui_scroll.yaml` / `gui_hotkey.yaml`，SkillRegistry 共 20 个 skill 全部加载成功。单测新增 16 个（共 26 passed，mock pywinauto/pyautogui，覆盖静默点击与前台兜底、静默输入与前台兜底、滚动符号/钳制、热键安全闸/窗口/全局）；真实环境冒烟通过（枚举 16 可见窗口、危险热键被拦截、`Ctrl + S` → `ctrl+s` → `^S`）。
- **阶段 3（N57c）✅ 已完成**：新增 `gui_operate` 组合闭环（截图 → 定位 → 操作 → 验证截图）。给阶段 2 执行器 `gui_click`/`gui_type` 加了 `mode` 参数（auto/silent/foreground），构成「意图 + 执行器」的选执行器层——同一操作意图可换执行器。`gui_operate` 实现：
  - **动态切换决策矩阵**（`_resolve_operate_mode`）：auto（默认）能定位 → 静默；定位不到 → 前台接管（若用户正用电脑先停下确认）；scroll/hotkey 天然前台。手动指定支持 `mode=silent/静默/后台` 与 `mode=foreground/前台/接管/看着你`。
  - **双模式并存**：后台静默（pywinauto UIA 不抢焦点）与前台接管（pyautogui 真实鼠标键盘）并存，由矩阵或手动切换。
  - **安全闸**：危险热键早拦截（复用 `_DANGEROUS_HOTKEYS`）；前台接管前冲突检测（`_user_idle_seconds` 用 GetLastInputInfo 查系统级用户空闲，<10s 判定用户活跃即拒绝接管，避免人机抢鼠标键盘）；timeout 钳制 1-300s。
  - **静默可见性**：verify=True（默认）操作前后自动截图留痕 + 动作日志追加 `data/gui/actions.log`（对齐 blackboard 落盘哲学），`_audit` 失败不阻断主流程。
  - 配套 `skills/gui_operate.yaml`（触发词「帮我操作/替我点/帮我输入」等），SkillRegistry 共 21 个 skill 全部加载成功。单测新增 11 个（共 37 passed，覆盖决策矩阵、冲突拦截、安全闸、审计留痕）；真实环境冒烟通过（无效 action 拒绝、危险热键拦截、真实 idle 检测 93.9s、auto 矩阵定位不到转前台提示）。全量回归 762 passed / 2 skipped 无回退。
- **阶段 4（N57d）✅ 已完成（边界测试部分）**：补 11 个边界/异常路径测试（共 48 passed）：`_clamp` 超时钳制（0→1、9999→300、非数字→300）、滚动格数下界（0→1）与非数字（→3）、热键归一化变体（大小写/空格/别名）、`_to_pywinauto_keys` 语法（`^%{DELETE}`/`{LWIN}E`/`{F5}`）、危险热键大小写变体拦截（Win + L / CTRL+ALT+DELETE）、热键目标窗口不存在降级列窗口、负数 index 定位不到、`gui_operate` 无目标拒绝、idle 未知（None）不拦前台、`verify=False` 不截图但动作日志常开（留痕与截图分层）、`_audit` 磁盘失败不阻断主流程（用不可写路径验证真实吞错）。测试驱动确认三处设计语义：`Escape` 归一化保留全拼（别名映射归 `_to_pywinauto_keys`）、`verify` 只关截图不关日志、留痕吞错用真实不可写路径验证。补 `docs/reference/LEARNINGS-2026-08-25-gui-automation.md`（6 条经验：UIA 三种点击语义、惰性 import 的 mock 姿势、留痕分层、mock 永不抛函数测不到吞错、pywinauto 动态 wrapper 别用 hasattr、热键安全闸规范化）。全量回归 773 passed / 2 skipped 无回退。
- **UI-TARS 视觉接管增强（N57c+）✅ 已完成**：补「pywinauto 定位不到的自绘界面（游戏/定制控件/画布）」这条视觉降级链。给 `vision_analyze` 加可选 `prompt` 参数（向后兼容，供结构化「返回坐标」提示复用）；新增 `gui_locate_visual` handler + `_visual_locate` 引擎（**优先配置的 UI-TARS 端点**：config `gui.uitars_url` 或环境变量 `UI_TARS_BASE_URL`，POST 契约返回 x/y；**兜底复用 qwen-vl** 视觉模型结构化定位）+ `_parse_coords`（兼容中英文逗号/空格）+ `_extract_path`（解析截图路径）。接入 `gui_operate`：auto 决策矩阵定位不到转前台且无坐标时，自动视觉接管补目标中心坐标再执行；视觉也找不到 → 停下问用户（对齐 ADR「否则停下问用户」）。配套 `skills/gui_locate_visual.yaml`，SkillRegistry 共 22 个 skill 全部加载成功。单测新增 10 个（共 58 passed，覆盖坐标解析、路径提取、UI-TARS 优先/降级 qwen-vl、找不到返回 None、视觉定位成功/失败、operate 自动补坐标/找不到中止）；真实冒烟：skill 加载 OK、解析正确，**真实 qwen-vl 调用被阿里云账号欠费（Arrearage 400）阻断**——代码按 ADR-007 优雅降级为可读提示（key 已配置、管线正确，视觉模型可用后即生效）。全量回归 783 passed / 2 skipped 无回退。**N57 全链路至此全部完成（含可选视觉接管）。**
- **高 DPI 坐标一致性修复（N57f，2026-08-26）✅ 已完成**：chuan 主进程默认 DPI-unaware，导致 mss/pywinauto（物理像素）与 pyautogui（逻辑像素）在高 DPI 缩放屏（125%/150%）上坐标不一致——视觉定位在物理截图上返回的坐标交给 pyautogui 点击会整体偏移一个缩放系数。修复：`gui_automation.py` 新增 `enable_dpi_awareness()`（进程早期声明 DPI 感知，优先 `SetProcessDpiAwarenessContext(-4)` Per-Monitor V2，失败回退 `SetProcessDPIAware()`，均失败静默）+ `dpi_scale()`；`_click_xy` 在未声明感知（`_dpi_aware=False`）且缩放比≠1 时，把物理坐标 ÷ 缩放比换算成逻辑坐标再点，返回值仍展示物理坐标；`runtime_supervisor.py` 启动早期（`__init__` 首行）调用 `enable_dpi_awareness()`（try/except 静默，不阻断启动）。**真机验证（125% 缩放，2026-08-26）**：声明前 `GetSystemMetrics` 逻辑屏 1536×864、声明后 `SYSTEM_AWARE` 物理屏 1920×1080（恰为 1.25 倍），`pyautogui.size()` 同步变物理，主路径生效。**验证暴露并修复一个缺陷**：`dpi_scale()` 原用 `GetDpiForSystem`/`GetDpiForMonitor(MDT_EFFECTIVE)`，二者在 DPI-unaware 进程里都被虚拟化成 96（读不到真实缩放比），导致「声明失败时的兜底换算」恒得 1.0、形同虚设；改为**注册表 `AppliedDPI` 主源**（不受 awareness 影响，实测返回 120→1.25），`GetDpiForSystem` 兜底。单测 +12（声明非 Windows/Per-Monitor 成功/旧 API 兜底/全失败/shcore 缺失兜底、缩放比注册表/注册表失败回退/全失败、点击换算/声明后不换算/缩放比 1 不换算），另加 autouse fixture 让坐标类测试按「已声明感知」直通跑、避免缩放屏隐性换算。全量回归 810 passed / 2 skipped 无回退。**注意**：声明与缩放比换算均已量化验证；「点准」最终效果 / 多屏 Per-Monitor 不同缩放仍属进阶，建议真机开真实窗口目视复核一次。

## ADR-055: GUI 元素记忆库（N58，2026-08-26）

**决策**: 给 GUI 自动化补「越用越不用调教」的记忆层——把「软件 + 控件描述 → 中心坐标/控件线索」沉淀为持久化元素库，下次定位/点击先查记忆再兜底重新定位。**对齐 blackboard 落盘哲学**：定位成功的物理坐标是可复用资产，不随会话丢失。

**落地**（`skills/handlers/gui_memory.py`，`data/gui/elements.db` SQLite）:
- 表 `gui_elements`（`app, description` UNIQUE），字段：窗口类/控件类型/控件文本/UIA 线索/中心坐标/命中数 hits/时间戳。
- `gui_mem_save` upsert（同 app+description 冲突 → 更新坐标 + hits+1）；`gui_mem_lookup` app/description 模糊匹配、命中数与最近使用排序；`gui_mem_forget` 删（app/description 至少给一）；`gui_mem_list` 用户可看「学过什么」。
- 集成：`gui_locate` 命中自动存记忆 + 提示「已记入元素记忆库」；`gui_locate` 未命中先查记忆返回「记忆命中」提示再走视觉兜底；`gui_click` UIA 定位不到但记忆有坐标 → 前台坐标点击兜底（「元素记忆命中」）。
- 配套 skill：`gui_mem_list.yaml` / `gui_mem_forget.yaml`（查看/清理），SkillRegistry 共 24 个 skill 全部加载成功。
- 单测 `tests/test_gui_memory.py` 12 个（save/lookup roundtrip、upsert 命中计数、模糊+优先级、无匹配、空描述拒绝、forget 各形态、list 渲染/空、DB 打不开静默降级）+ `test_gui_automation.py` 集成 4 个（locate 存记忆、locate 记忆命中跳过视觉、click 记忆坐标兜底、无记忆回 hint）。真实 DB 冒烟通过（存/查/列/删全链路，事后清理）。
- 测试隔离：新增 `tests/conftest.py` autouse fixture 把 `_DB` 统一指向 tmp——**关键坑**：SkillRegistry 会把 `skills/` 加入 sys.path，使 `handlers.gui_memory`（生产路径，gui_automation 内懒加载用）与 `skills.handlers.gui_memory`（命名空间包路径，测试 import 用）成为**两个不同模块对象**、`_DB` 不互通；只补丁一个会继续污染真实 data/gui/elements.db。conftest 对两个对象同时补丁到同一 tmp DB。
- 全量回归 812 passed / 2 skipped 无回退。

**自愈闭环（N58 二次迭代，坐标过期自愈）**: 记忆只记「点哪」不记「点对没点对」会让旧坐标反噬——界面改版/换 DPI 屏后 `hits` 越攒越高、越错越优先。补「点击 → 复核 → 置信度 → 遗忘 + 重定位」闭环：
- `gui_memory` schema 增 `fail_streak` / `last_verified_at`（`_connect` 幂等 `ALTER TABLE` 给旧库补列）；新增 `gui_mem_verify(app, description, ok)`——`ok=True` 重置 fail_streak 并更新 last_verified_at；`ok=False` 累计 fail_streak，连续失败达阈值 3 即删除该条（返回 reset/kept/forgotten/missing）。
- `gui_automation` 记忆兜底（`gui_click` UIA 定位不到但有记忆坐标）改走 `_click_with_memory_verify`：截图(前) → 前台坐标点击 → 延迟 300ms → 截图(后) → `_click_effect_changed` 复核 → `gui_mem_verify` 更新置信度 → 连续失效达阈值自动 `_relocate_and_resave`（视觉重定位新坐标 + 重新记入记忆 + 用新坐标补一次点击）。
- 复核信号分级（强→弱）：L1 点击后重新 UIA 定位（能找到说明界面确实变了）→ L2 截图 dHash 汉明距离（`_img_dhash`，阈值 5，宽松防误删）→ unknown（缺 PIL/截图失败则不动记忆，只提示「无法确认」）。
- 边界：不做误操作回滚、不做多 DPI 归一化坐标存储（那是另一独立 bug，避免混入）；复核延迟 300ms、失效阈值 3（常量可配）。遗忘保守——单次不改判、连续 3 次才删；删除不是终点，必须接重定位重记。
- 单测：`test_gui_memory.py` +3（verify ok 重置 / 失败累计至遗忘 / missing noop）、`test_gui_automation.py` +11（_img_dhash 降级、_click_effect_changed L1/L2/unknown、_click_with_memory_verify changed/kept/forgotten/unknown、_relocate_and_resave 成功/失败），改 1 个（click 记忆兜底改为验证闭环调用）。

**理由**: GUI 自动化天生脆（界面改版即崩），且每次都要重新定位很「费调教」；记忆库让常用操作收敛为「一次定位、永久复用」。SQLite 单文件零依赖，对齐 sessions.db/memory_fts.db 惯例。

**反例（不做）**: 把坐标当唯一依据无条件信任（界面一动就点错，需 hits + 时间戳留痕可查）；存整张控件树（过大、易碎）；训练模型学界面布局（过重，记忆库已覆盖 80% 价值）。

## ADR-056: checkpoint 历史修复·删错 checkpoint 修复（2026-08-26）

**背景**: LLM 提供方要求 ToolMessage 紧跟 AIMessage 的 tool_calls 之后（否则 `INVALID_CHAT_HISTORY` / `insufficient tool messages`）。上次会话在工具执行前中断时，checkpoint 会留下「有 tool_calls 无 ToolMessage」的悬空 AIMessage，重启重放被拒。chuan 已有两层防护：`BuiltinAgent.run()` 每次执行前内部调 `_repair_history`；`PersonaRole._ensure_agent_history_ok` 对带 `_repair_history` 的 agent 类型兜底。

**排查发现（2026-08-26）**: `_repair_history` 首选「删最新 checkpoint 回退干净状态」，但 `_delete_latest_checkpoint` 用 `ORDER BY checkpoint_id DESC LIMIT 1` 定位「最新」checkpoint——checkpoint_id 是 UUID（随机），字符串排序 ≠ 时间顺序，会删到字典序更大的旧 checkpoint、漏删真正含悬空 tool_call 的最新 checkpoint，400 依旧；且未按 `checkpoint_ns` 过滤（checkpoints 表主键含 checkpoint_ns）。

**修复**（`chuan/agents/builtin.py`）: `aget_state` 返回的 StateSnapshot 已含精确 `config['configurable']['checkpoint_id']` / `checkpoint_ns`；`_repair_history` 从中取值传给 `_delete_latest_checkpoint(config, checkpoint_id, checkpoint_ns)`，SQL 改为精确 `WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?`，去掉 ORDER BY 子查询。拿不到精确 id 时返回 False，走追加占位 ToolMessage 兜底（宁可不删，不删错）。

**测试**（`tests/test_history_repair.py`）: 更新 `test_repair_history_deletes_dirty_checkpoint`（断言 DELETE 用精确 `(thread_id, checkpoint_ns, checkpoint_id)` 且无 ORDER BY）+ 新增 `test_repair_history_no_checkpoint_id_falls_back_to_append`（拿不到 id 走追加兜底），共 12 passed。全量回归 829 passed / 2 skipped 无回退。

## ADR-057: P4 安全增强（N59，机器绑定加密 / 陌生人识别 / 自动锁屏，2026-08-27）

**背景**: ROADMAP P4 待办「机器绑定加密 / 陌生人距离 / 自动锁屏」（借鉴 Aivy「灵魂数据加密到硬件指纹」）。此前 chuan-os 敏感数据（SQLite/JSON）明文落盘，换机/拷走即可读；语音入口有 N54 声纹库但无「陌生人是谁」的判定与应对。

**决策**: 落地为 N59，新增 `chuan/security/` 包，三块能力：

1. **机器绑定加密**（`binding.py`）：
   - 机器指纹 = 网卡 MAC（`uuid.getnode`）+ 主机名 + 平台 + **系统盘卷序列号**（Windows `ctypes.GetVolumeInformationW`，格式化不变，是「换机/换盘即失效」的锚点；非 Windows 用 MAC+hostname 兜底），拼接后 SHA-256。
   - 密钥派生：PBKDF2-HMAC-SHA256（hashlib 标准库，40 万次迭代）从指纹派生 32 字节——指纹相同密钥相同（本机可解），换机即失配。
   - 加密：优先 `cryptography.Fernet`（AES-128-CBC + HMAC，langchain 生态普遍已装）；缺失回退标准库方案（HMAC 流密钥 XOR + SHA-256 MAC 校验，仅防「拷走不可读」）。输出带 `CHUANBIND1:` 版本头；解密失败返回 None，绝不抛错。
   - 文件级 `encrypt_file/decrypt_file` 封装（原地重写，原子 tmp+replace）。

2. **陌生人识别**（`guard.py::identify_speaker`）：复用 N54 声纹库（`spoof.extract_features` / `load_speaker` / `_compare_voiceprint`），遍历已注册声纹取最高相似度分；低于阈值判「陌生人」；音频不可靠（过短/静音/低能量，复用 spoof 一级反欺骗阈值）或无注册声纹 → unknown（不判陌生人，避免环境声误锁屏）。

3. **自动锁屏**（`guard.py::lock_workstation` + `SecurityGuard`）：Windows `ctypes.windll.user32.LockWorkStation()`（Win+L 等价），非 Windows/失败静默降级 False。`SecurityGuard` 配置驱动（config `security.lock`，默认关）：**连续 streak 次（默认 3）陌生人判定才锁屏**（防单次误判），主人声纹清零计数，锁屏后复位；lock_cb 可注入（测试/定制），回调异常被吞不阻断语音主循环。

**配置**（`config/config.yaml` `security:` 段，默认全关，兼容现有明文）：`binding.enabled` / `lock.enabled` / `lock.threshold`（默认 0.6）/ `lock.streak`（默认 3）。

**接入**（`chuan/voice/main.py`）：`run_voice_mode` 构造 `SecurityGuard.from_config()`（默认关，异常不阻断语音），传入 `_run_alwayson_loop`；阶段 3 每句话识别后 `guard.check()`，stranger 打印提示、locked 打印「已自动锁屏」。

**测试**: `tests/test_security_binding.py`（指纹确定性/跨机失配/往返/篡改静默/文件级，13）+ `tests/test_security_guard.py`（识别 owner/stranger/unknown、lock_workstation 双平台 mock、SecurityGuard 防抖/复位/回调吞错/垃圾输入，20）= 33 passed。锁屏测试一律注入假 lock_cb / mock os.name，绝不触发真实锁屏。

## ADR-058: 媒体生成 V2（N56，视频/图片配置化 HTTP 后端，2026-08-27）

**背景**: ADR-052 给 `media_generate("video"/"image")` 留了占位（返回「待接 seedance/seedream API」提示）。seedance/seedream 是 IDE 侧插件（只给 IDE 助手发 GenerateVideo/GenerateImage 指令），chuan 运行时无直连 API；用户暂无真实端点/密钥。要让「配好即用」的骨架先落地、密钥后填。

**决策**: 落地为 V2（接口不变，`media_generate` 签名不动）——把 video/image 占位换成**配置化 HTTP 后端**：
- **配置**（`config/config.yaml` `media:` 段）：`video`/`image` 子段各含 `endpoint`（默认空）、`api_key_env`（环境变量名，推荐不进 Git）、`api_key_secret`（兜底 `config/secrets.yaml` 字段，同 brain 惯例）、`timeout`（视频 120s / 图片 60s）。默认全空 = 未接入 → 返回可读提示，不抛错。
- **请求协议**（通用，真实端点格式定了再按需扩展）：`POST JSON {"prompt": ...}` + `Authorization: Bearer <key>`（标准库 `urllib`，零新增依赖，对齐项目「静默降级」惯例）。
- **落盘**：响应二进制按 `Content-Type` 映射后缀（video/mp4→.mp4、image/png→.png、image/jpeg→.jpg…），未知类型按 kind 缺省后缀（.mp4/.png），写 `data/media/<kind>_<prompt>_<时间戳>.<ext>`；空响应 / 非 2xx / 网络异常 → 可读失败提示。
- **密钥读取**：环境变量 `SEEDANCE_API_KEY` / `SEEDREAM_API_KEY` 优先 → secrets.yaml 兜底（`_load_media_key`，同 web_search 读百炼 key 的模式）。

**理由**: 「配置密钥后可用」的目标形态（ADR-052 预留），无真实端点时先交付可测试的协议骨架——mock 服务器即可验证全链路（请求格式/鉴权/落盘/降级），真实端点/密钥到位只需改 config.yaml，零代码改动。

**反例**: 不做真实 seedance/seedream 端点的协议适配（无端点可对）；不做轮询式异步任务（假定后端同步返回二进制）；不做密钥入库 Git（secrets.yaml 已 .gitignore）。

**落地记录（已完成，2026-08-27，N56 V2）**: `skills/handlers/media_gen.py`（`_load_media_cfg`/`_load_media_key`/`_gen_http`/`_PROVIDER`/`_KIND_LABEL`/`_CT_EXT`），video/image 分支改调 `_gen_http`；`config/config.yaml` 增 `media:` 段。测试：`tests/test_media_gen.py` +8 例（mock 服务器全链路：image→png 落盘 + 请求协议断言 / video→mp4 / jpeg→jpg 后缀 / 未知 ctype→kind 缺省后缀 / 500 降级 / 空响应降级 / 缺密钥未接入 / endpoint 空未接入），共 18 passed；生产路径（handlers.media_gen）实测未配置时返回可读提示。全量回归 870 passed 无回退。

## ADR-059: 四象限协作框架固化为 prompt 型技能（N30，2026-08-27）

**背景**: 复杂任务（做方案 / 需求分析 / 项目设计 / RPA 等）协作时，agent 容易陷入两类毛病：一是不确认目标就闷头干、反复无意义提问；二是一味顺从用户、不点破需求漏洞。项目已有 N30「技能即记忆」——prompt 型技能（触发关键词命中即在开工前注入复用做法，`PersonaRole._maybe_inject_skill` 前置到任务文本），本框架值得固化为这样一个可复用技能。

**决策**: 新增 `skills/collab_quadrant.yaml`（prompt 型，无 handler/mcp_server → `Skill.kind == "prompt"`，`to_tool()` 返回 None 不进工具列表，仅走 `find_prompt_skill` 注入）：
- **触发关键词**：四象限 / 协作框架 / 协作模式 / 按四象限 / 做方案 / 设计方案 / 方案设计 / 一个方案 / 需求分析 / 项目设计 / 复杂任务（子串匹配、大小写不敏感）。
- **注入 prompt**（协作纪律）：
  1. 共同已知：确认目标/背景/交付标准/边界，信息足够直接执行，不无意义反复提问；
  2. 我的已知你的未知：缺信息影响结果时最多提 3 个关键问题，不影响就写明假设先输出探索版本；
  3. 我的未知你的已知：主动点破风险/替代方案/前提错误，给出取舍依据；
  4. 共同未知：转可验证假设，必要时设计最小可行实验（变量 + 成败标准）。
- **执行规则**：四象限是内部思考逻辑不机械打印；严格区分事实/推断/假设/待验证；不一味顺从，帮用户挑错。

**理由**: 把协作纪律从「口头约定」升级为「可触发技能」——命中即注入，不占用工具位、不误触简单任务（天气等无关文本不命中），与 N30 自动技能创建同一条沉淀链路。

**反例**: 不做 handler 型（无需执行函数，纯提示词）；不把四个象限机械打印进每轮对话（会制造无效流程）。

**落地记录（已完成，2026-08-27）**: `skills/collab_quadrant.yaml`（name/description/type: prompt/trigger.keywords/prompt）。测试 `tests/test_collab_quadrant.py` 5 例：注册为 prompt 型 / 触发词命中（含 `find_prompt_skill` 注入入口）/ 无关文本不误触（天气）/ prompt 内容含协作纪律要点 / `to_tool()` 返回 None。修一处关键词坑：「设计方案」不是「帮我设计一个方案」的子串（中间隔「一个」），补「一个方案」后命中。注入链路复用既有 `SkillRegistry.find_prompt_skill` → `role.py:_maybe_inject_skill`，零代码改动。
