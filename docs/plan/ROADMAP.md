# 开发路线 ROADMAP

chuan-os 按节点开发，每个节点标「开发用模型」——即**写该段代码时用的 IDE 模型**，不是 chuan 运行时的脑。
模型档位镜像 chuan 自身三档脑：**Deep=承重墙、Balanced=日常、Fast=小修**。

## 节点总览（N0–N23，全部 ✅ DONE）

| 节点 | 一句话 | 阶段 | 状态 |
|------|--------|------|------|
| **N0 地基** | pyproject / venv / 能启动 | 基础班底 | ✅ |
| **N1 大脑层** | 三档脑统一 `.complete()` | 基础班底 | ✅ |
| **N2 工具+MCP** | MCP/skill 挂载 + 全局注册表（ADR-009） | 基础班底 | ✅ |
| **N3 persona 出生** | YAML → `create_react_agent` 子图 | 基础班底 | ✅ |
| **N4 幕僚长** | `create_supervisor` + 路由 | 基础班底 | ✅ |
| **N5 封驳关** | guard 方案审核（ADR-008） | 基础班底 | ✅ |
| **N6 记忆层** | 长期检索 + 共享黑板 | 基础班底 | ✅ |
| **N7 外来 agent 融合** | `agents/` 加载为 worker/tool | 基础班底 | ✅ |
| **N8 管家主动触发** | 定时任务 / 主动预警 | 基础班底 | ✅ |
| **N9 端到端联调** | CLI 交互循环跑通 | 基础班底 | ✅ |
| **N10 测试+文档** | `tests/` + README | 基础班底 | ✅ |
| **N11 修复启动+基础工具** | MCP filesystem/weather/opencode + bash 安全 | 架构升级 | ✅ |
| **N12 会话持久化+sub_agent** | AsyncSqliteSaver + call_pi 工具 | 架构升级 | ✅ |
| **N13 三层记忆体系** | 短期会话 + FTS5 长期 + 黑板 | 架构升级 | ✅ |
| **N14 SOUL.md 角色驱动** | 目录格式 + MEMORY.md（ADR-013） | 架构升级 | ✅ |
| **N15 语音交互闭环** | 唤醒 + STT + TTS 全双工（ADR-011） | 架构升级 | ✅ |
| **N16 角色音色+音效** | voices.yaml + 程序化音效 | 架构升级 | ✅ |
| **N17 TUI 终端界面** | Textual 调试界面 | 架构升级 | ✅ |
| **N18 Gateway 拆分** | 七大组件（ADR-012） | 架构升级 | ✅ |
| **N19 多角色并行+微信** | 岗位化调度 + 微信远程（ADR-014/015） | 架构升级 | ✅ |
| **N20 自改进 GEPA** | assess/preserve 沉淀 MEMORY.md | 架构升级 | ✅ |
| **N21 后台委派 harness** | 派发即返 + 任务状态机/DAG（ADR-016） | 架构升级 | ✅ |
| **N22 MCP 管理面板** | `/mcp` 连接状态 + 启停（ADR-017） | 架构升级 | ✅ |
| **N23 监督者全监控** | 死胡同检测 + redirect + HUD（ADR-018） | 架构升级 | ✅ |
| **N24 Wiki 知识库** | 实体归并 + index/log + 维护层（ADR-019） | 记忆升级 | ✅ |
| **N33 ACI 预判注入** | 路由前并行预取记忆+wiki 上下文注入岗位（ADR-028） | 架构升级 | ✅ |
| **N34 HUD SCENE 协议** | core 持 scene → UI 纯投影（hello/welcome + 全量 + patch，ADR-029） | 架构升级 | ✅ |
| **N35 任务断点续跑** | 打断不丢工具：子任务结果缓存 + /resume 复用（ADR-030） | 架构升级 | ✅ |
| **N36 外接知识库检索** | search_vault/list_vaults：临时查外置 Obsidian 库，与内部记忆隔离（ADR-031） | 记忆升级 | ✅ |
| **N37 岗位化 1:N 过渡** | 岗位多 agent 池 + 会话级状态隔离（spawn_agent/agent_count + 多会话并行不串扰，ADR-032） | 架构升级 | ✅ |
| **N38 岗位化 1:N·第二台阶** | 1:N 默认启用：并行 auto 子任务独立 worker 实例 + 实例 id 可指定（ADR-033） | 架构升级 | ✅ |
| **N39 岗位化 1:N·第三台阶** | 按实例配置工具/模型/记忆：RoleAgentConfig 贯穿 spawn 与 worker（ADR-034） | 架构升级 | ✅ |
| **N40 按复杂度选实例·声明式** | config.yaml role_instances：按 simple/medium/heavy 选实例（ADR-035） | 架构升级 | ✅ |
| **N41 动态实例池·自动扩缩容** | config.yaml role_instances.pool：min/max 容量 + 空闲 TTL 自动回收（ADR-036） | 架构升级 | ✅ |
| **N42 岗位间协作·第五台阶** | 多岗位并行编排 + 共享黑板：一任务拆多岗并行，黑板落盘聚合（ADR-037） | 架构升级 | ✅ |
| **N43 记忆语义检索·sqlite-vec** | FTS5 词法 + 向量语义双路合并召回：sqlite-vec 向量索引旁路，嵌入源可注入/config 云端（ADR-038） | 记忆升级 | ✅ |
| **N44 Redis TTL 缓存旁路** | cache-aside 加速：天气/搜索结果 TTL 缓存，Redis 后端 + 内存兜底 + 故障降级（ADR-039） | 架构升级 | ✅ |
| **N45 任务队列+事件总线** | Redis Streams 可靠任务队列 + Pub/Sub 事件总线，跨进程通信与后台任务可靠执行（ADR-040） | 架构升级 | ✅ |
| **N46 本地资源感知采集器** | 系统/桌面/SSH/Git 状态确定性采集 handler skill（纯标准库 + ctypes，失败静默降级，ADR-041） | 资源感知 | ✅ |
| **N47 HTTP API Gateway** | FastAPI 客户端/服务器解耦接入层：/health + /api/chat（ADR-042） | 架构升级 | ✅ |
| **N48 局域网 HTTPS + 手机 PWA 接入** | HTTP/HTTPS 网关 + PWA（manifest/SW）+ SCENE WebSocket：手机同局域网 HTTPS 访问并下发/接收 HUD 命令 + 顺手修正文档口径（ADR-043） | 架构升级 | ✅ |
| **N49 vault MCP server** | 外来 agent 经 MCP 检索/写入共享黑板：search_vault/write_vault/list_vaults 读写 data/teams/ 黑板（ADR-044） | 架构升级 | ✅ |
| **N50 视觉理解** | 图片/截图视觉分析 handler skill：vision_analyze 本地图/URL → qwen-vl 视觉模型返回描述，key 复用百炼、静默降级（ADR-045） | 能力增强 | ✅ |
| **N51 工具市场** | 能力目录 + 运行时按信号裁剪：catalog/enable/disable + select(task) 确定性裁剪工具子集，默认关闭对齐 ADR-009（ADR-046） | 架构升级 | ✅ |
| **N52 视觉理解 V2** | 扩展 vision_analyze：视频/录屏 ffmpeg 抽首帧 + PDF/表格转图后走视觉分析，缺依赖静默降级（ADR-047） | 能力增强 | ✅ |
| **N54 声纹防欺骗** | 声纹注册 enroll_speaker + 反欺骗 anti_spoof（V1 规则版：静音/时长/能量 + 已注册声纹比对，float32/int16 缩放兼容，静默降级，ADR-049） | 安全 | ✅ |
| **N55 向量 RAG 评估闸门** | 确定性量化记忆库规模（内部+外接 .md 篇数/字符）对比阈值 + 漏召回案例留痕，三态判定是否启动本地 embedding+faiss 评估（ADR-050） | 记忆升级 | ✅ |
| **N56 媒体生成** | 音乐程序化合成写 wav（numpy+wave 零依赖，情绪影响调式速度）+ 视频/图片后端占位（待接 seedance/seedream，ADR-052） | 能力增强 | ✅ |

> 各阶段明细见下文「N0–N10 节点计划（基础班底阶段）」与「N11–N23 节点计划（架构升级阶段）」。

## N0–N10 节点计划（基础班底阶段）

| 节点 | 干什么 | 用模型 | 说明 |
|------|--------|--------|------|
| **N0 地基** | 补全 pyproject 依赖、建 venv、`python -m chuan.main` 能启动 | Fast | 机械活 |
| **N1 大脑层** | `brains` 加载（openrouter/ollama），统一 `.complete()`，读 config 三档 | Balanced | 标准实现 |
| **N2 工具+MCP** | `mcp_adapter` 加载 `mcp_servers/`；`skill_loader` 把 skill 挂成 tool/prompt；落实 ADR-009 全局注册表 | Balanced | 接外部能力 |
| **N3 persona 出生** | `persona_loader.birth()`：读 YAML → `create_react_agent` → 子图；处理 `deny` 减法 | Deep | 核心，写错全歪 |
| **N4 幕僚长** | `runtime_supervisor`：`create_supervisor(workers=...)` + `task_routes` + LLM 兜底 | Deep | 架构承重墙 |
| **N5 封驳关** | `guard.py`：方案→审核→approve/reject→打回，嵌执行边之前（ADR-008） | Deep | 安全机制不能错 |
| **N6 记忆层** | `memory.py`：Obsidian RAG 召回 + 共享黑板 namespace 隔离 | Balanced | |
| **N7 外来 agent 融合** | 读 `external_agents.enabled`，加载 `agents/<name>/`；`prompt`→birth 成 worker，`command`→包成 tool（MCP/stdio） | Deep | 协议融合最易出 bug |
| **N8 管家主动触发** | scheduler / 定时任务 / 主动预警（housekeeper 非响应式能力） | Balanced | |
| **N9 端到端联调** | `main.py` 进交互循环，幕僚长醒来，跑通一个完整请求 | Balanced/Fast | 杂活多 |
| **N10 测试+文档** | `tests/`、`README` 更新 | Fast | **DONE** |

## 依赖关系（谁卡谁）

```
N0 → N1 → N2 → N3 → N4 → N5 → N6 → N7 → N8 → N9 → N10
```

**关键路径**：N3 → N4 → N5 串行，必须用 Deep 写、写完自测再过下一关。
persona 不出声、幕僚长不路由、封驳不拦，后面全白搭。

## 当前进度

- **N0 已完成**：venv 建在 `.venv/`（系统 Python 3.13.14），12 个依赖全部装好，`python -m chuan.main` 能启动并打印 banner。期间发现并修复了 pip 中断导致的 `~`-前缀半成品目录（命名空间包假象），最终用干净重建 + 单趟安装解决。`pyproject.toml` 的 build-backend 已纠正为 `setuptools.build_meta`。
- 已实现：目录骨架、14 个 persona、skills/mcp_servers 桩、`DECISIONS.md` ADR-001~009、本 ROADMAP
- **N1 已完成**：`chuan/brains.py` 实现 `Brain` + `BrainRegistry`，读 `config/config.yaml` 三档配置 + `config/secrets.yaml` API key，统一 `.complete()` 接口（字符串/消息列表进，字符串出）。OpenRouter → `ChatOpenAI`，Ollama → `ChatOllama`，都兼容 LangGraph。`chuan/__init__.py` 暴露 `Brain`/`BrainRegistry`。
- **N2 已完成**：`chuan/adapters/mcp_adapter.py` + `chuan/adapters/skill_loader.py` 实现。
  - `MCPAdapter`：读 `config/mcp_servers.yaml`，用 `langchain-mcp-adapters` 的 `stdio_client` + `load_mcp_tools` 将 MCP server 工具转为 LangChain Tool；支持 `connect_all()`/`disconnect_all()` 生命周期；单 server 失败不阻断其他。
  - `SkillRegistry`：加载 `skills/*.yaml`，解析 trigger/mcp_server/handler 三种形态；handler 类型通过 `importlib` 动态加载 Python 函数并包装为 Tool；自动将 skills/ 目录加入 `sys.path` 解决 handler import 路径问题。
  - `ToolRegistry`（ADR-009 核心）：统一组装 Skill(handler) + MCP tools；`get_tools(deny=[...])` 按 deny 列表做减法过滤（skill 名和 MCP server 名都可写进 deny）。
- **N3 已完成**：`chuan/persona_loader.py` 实现 `Persona` + `PersonaLoader`。
  - `Persona`：persona YAML 的「设备描述符」，自动识别两种格式——ADR-009 新格式（`role` + `deny` 减法）和旧格式（`tools`/`skills` 白名单），`uses_legacy_allowlist` 属性区分。`build_system_prompt()` 生成角色身份+职责+边界约束。
  - `PersonaLoader.birth(name)`：**核心** —— 读 YAML → 取 brain → 解析工具 → `create_react_agent(...)` → 返回 `CompiledStateGraph`。这一刻 agent 才活了。支持 `checkpointer`（N6 记忆接入点）和 `force_rebirth`。
  - `_resolve_tools()`：新格式走全局挂载 + deny 减法；旧格式走白名单过滤。两种格式并存，无需回填即可工作。
  - `birth_all(exclude=[...])`：批量出生，N4 幕僚长的入口。
  - `role_map()`：role → persona 映射，N4 路由用。`kill(name)`：对应 ADR-007 用完即焚。
- **N4 已完成**：`chuan/runtime_supervisor.py` + `chuan/orchestrator.py` 实现。
  - `RuntimeSupervisor`：幕僚长核心，基于 `langgraph-supervisor.create_supervisor()` 实现。`wake_up()` 出生所有 worker（排除自己）并编译 supervisor 图；`dispatch(message)` 分发用户消息到合适 worker；`shutdown()` 清理资源。路由 prompt 自动从 `chief_of_staff.yaml` 的 `routing` 配置构建，包含显式锁定/关键词/兜底规则。
  - `Orchestrator`：轻量路由器，基于规则的快速路由层。优先级：显式锁定 > 关键词计分 > LLM 兜底（返回 None）。`route(message)` 返回目标 persona 名或 None。减少不必要的 LLM 调用开销。
  - `chuan/__init__.py` 更新导出 `RuntimeSupervisor` 和 `Orchestrator`。
- **N5 已完成**：`chuan/guard.py` 实现 ADR-008 封驳关。
  - `Guard`：安全闸核心，实现「规划 → 审核 → 执行」三段式的审核段。`review(agent_name, action)` 返回 `GuardResult`（approve/reject + reason）。
  - 内置 11 条危险模式检测规则：文件删除(rm -rf)、系统破坏(format/shutdown)、数据库破坏(DROP/TRUNCATE)、PII泄露、网络攻击(nmap)、远程代码执行(curl|bash)等。
  - `review_batch()` 批量审核支持短路模式（strict_mode 下首个 reject 即停）。
  - `as_post_model_hook()` 可直接作为 langgraph-supervisor 的 `post_model_hook` 使用，在 LLM 决策后、实际转交前进行安全审核。
  - 规则管理：`add_pattern()` / `remove_pattern()` / `list_patterns()` 支持运行时动态调整。
  - `RuntimeSupervisor` 已集成 Guard：构造时传入 `guard` 参数（默认自动创建），`wake_up()` 时自动挂载为 post_model_hook。
  - `chuan/__init__.py` 更新导出 `Guard`、`GuardResult`、`GuardAction`。
- **N6 已完成**：`chuan/memory.py` 实现本地 Markdown 记忆层。
  - `Memory.remember()` / `recall()`：长期记忆保存到 Obsidian-compatible vault 的 `notes/`；无网络、无模型依赖的本地短语/词频检索提供稳定的 RAG 基线。
  - `write_blackboard()` / `read_blackboard()` / `list_blackboard()`：共享黑板位于 `shared/`；外来 agent 自动隔离到 ADR-006 规定的 `shared/external/<name>/`。
  - 所有记忆名称与命名空间均校验，阻止 `..` 等路径穿越；`RuntimeSupervisor` 将同一个 `InMemorySaver` checkpoint 注入 worker 与 supervisor，`dispatch(..., session_id=...)` 隔离会话状态。
- **N7 已完成**：`chuan/external_agents.py` 实现显式外来 agent 融合。
  - 仅加载 `config.yaml` 中 `external_agents.enabled` 列出的 `agents/<name>/agent.yaml`，不扫描或自动发现目录；名称必须匹配配置项，内部 persona 同名时拒绝加载。
  - prompt 型外来 agent 直接转为 worker；声明 `command: [executable, ...]` 的 agent 也会直接编译为独立 worker，供幕僚长路由。
  - command worker 以 stdin/stdout 协议运行，始终 `shell=False`、限制超时（1–600 秒）并在执行前经过 `Guard`；命令输出或失败原因作为该子 agent 的回复回传。
- **N8 已完成**：`chuan/scheduler.py` 实现管家主动触发。
  - `ProactiveScheduler` 支持显式配置的间隔任务、后台轮询或 CLI 同步 `run_pending()`、提醒队列和可注入的通知回调。
  - 到期任务通过 `RuntimeSupervisor.dispatch_to()` 直接交给指定 worker（默认 `housekeeper`），各任务使用独立 `proactive:<job>` 会话。
  - 配置默认关闭；单任务异常转为提醒而不会停止后续调度。`RuntimeSupervisor.shutdown()` 会清理后台调度线程。
- **N9 已完成**：`chuan/main.py` 实现端到端 CLI 交互循环。
  - 启动时唤醒幕僚长，普通输入经 `dispatch()` 进入 supervisor；`/workers` 查看当前 worker，`/alerts` 读取主动提醒，`/help` 查看命令。
  - 可测试的 `run_cli()` 支持注入 supervisor、输入与输出；EOF、Ctrl+C 与 `exit` 均会调用 `shutdown()` 清理 worker 和调度器。
- **N10 已完成**：新增 5 个测试文件（`test_brains.py`、`test_guard.py`、`test_persona_loader.py`、`test_orchestrator.py`、`test_skill_loader.py`），共 85 个测试通过（2 个需 API key 的 skipped）。覆盖：大脑注册表与统一接口、安全闸 11 条规则与批量审核、persona 双格式解析与出生、轻量路由关键词与显式锁定、skill 注册表与工具过滤。补全所有 legacy persona 的 `role` 字段，更新幕僚长路由规则覆盖全部 14 个角色。
- **N17 已完成**：TUI（Textual）水线居中、左侧角色面板（14 角色 ASCII 小像+专属色）、工具调用/记忆召回事件流、巩固状态入状态栏、命令面板（Ctrl+P）与 Esc 软中断、顶栏「川」字标与中文标签。
- **N18 已完成**：Gateway 七大组件拆分落地（ADR-012）。`chuan/gateway/` 七组件 + `runtime_supervisor.py` 重构为组装入口；`tests/test_gateway_components.py`（11 例）覆盖，全量 257 passed。详见 DECISIONS.md ADR-012 落地记录。
- **N19 已完成**：岗位化调度（ADR-014）落地。`chuan/role.py`（PersonaRole：规划门槛/任务拆分/拓扑分波 `asyncio.gather` 并行/确定性汇总/重试/退化检测/specialist spawn）+ `chuan/agent_pool.py`（常驻池+动态 spawn）+ `chuan/team_state.py`（磁盘真相冷恢复）+ `chuan/team_bus.py`（`ask_role` 成员直通，一层协作防递归）。`tests/test_role.py` 覆盖。微信远程操控电脑（ADR-015）落地：`chuan/channels/wechat.py`（`WeChatChannel` 收→路由→回发 + 企业微信应用消息发送 + 会话按 wechat:<id> 隔离），`tests/test_wechat.py` 覆盖。
- **N20 已完成**：GEPA 自改进循环落地。`chuan/self_improve/gepa.py`（`assess` 确定性评估 + `preserve` 追加 MEMORY.md + `run_gepa` 编排）；`PersonaRole.dispatch` 经 `_wrap_result` 挂接自动自改进（旁路，异常不阻断）；仅 ADR-013 目录格式角色生效（当前全部 14 个角色）。`tests/test_self_improve.py`（9 例）覆盖，全量 266 passed。
- **N11 已完成**：启动报错修复 + 基础工具。删除 `_create_call_agent_tool` 残留；路由默认脑切百炼 `bailian_flash`（绕开 glm-4-plus 429）；MCP `filesystem_server.py`/`weather_server.py`/`opencode_wrapper.py` 全部实现（read_file/write_file/list_dir/delete_file/get_weather/run_opencode）；`skills/handlers/bash_safe.py` 两层安全闸 + 超时；另建 `chuan/tools/builtin_tools.py` 纯 `@tool` 兜底。
- **N12 已完成**：会话持久化 + sub_agent 链路。`Memory` 改用 `AsyncSqliteSaver`（重启不丢会话）；`PersonaLoader._resolve_sub_agent_tools()` 按 `sub_agents` 生成 `call_pi`/`call_prime_agent`/`call_claude_code`/`call_opencode` 工具并注入角色工具列表。
- **N13 已完成**：三层记忆体系。短期会话（AsyncSqliteSaver）+ 长期检索（FTS5 全量索引 `data/memory_fts.db`，写时增量 + 失败回退全盘扫描）+ 共享黑板（Obsidian `shared/`，namespace 隔离）。
- **N14 已完成**：SOUL.md 角色驱动（ADR-013）全员迁移落地。14 个角色全部从单文件 `personas/<name>.yaml` 迁到目录 `personas/<name>/`（`config.yaml` + `SOUL.md` + 运行时 `MEMORY.md`），旧 `.yaml` 全部删除；`PersonaLoader` 双格式兼容保留，目录角色额外注入 `read_role_memory`/`append_role_memory` 私有记忆工具。`tests/test_persona_loader.py`（`test_directory_format_persona_loading` 等）覆盖。
- **N15 已完成**：语音交互闭环（ADR-011）落地。`chuan/voice/`：`stt.py`（faster-whisper 本地缓存 + HF 镜像 + openai-whisper 回退）、`tts.py`（edge-tts + SAPI/say/espeak 回退，异步播报 + 代际 `stop()` 打断）、`wake_word.py`（OpenWakeWord，缺依赖回退按键）、`sounds.py`（程序化事件音效 + 角色音色映射）、`main.py`（常开麦克风流 + barge-in 语音打断 + 静音收尾 + 退出口令）；`config/voices.yaml` 14 角色音色。入口：`chuan-voice` 脚本 / CLI `/voice` / `python -m chuan.voice`。`tests/test_voice.py`（32 例）覆盖。
- **N16 已完成**：角色独立音色 + 音效素材（借鉴 assistant-x-openclaw）。`config/voices.yaml` 14 角色→edge-tts 中文音色映射；`VoiceFeedback` 门面（`play` 事件音效 + `voice_for_reply` 从「[角色]」前缀解析音色、正文剥前缀）；`tts.speak(voice=)` 角色化播报。事件音效走程序化合成（`SoundEngine`，init/listen/bargein/thinking/success/error/exit 七声，正弦+ADSR、零 wav 素材，0.5s 同事件防抖 + `CHUAN_SOUNDS=0` 静默降级）。`tests/test_voice.py` 覆盖（含 thinking 事件）。
- **N21 已完成**：后台委派 harness（ADR-016，借鉴 deepseek-harness）落地。`chuan/gateway/agent_harness.py`（`AgentHarness`：`submit()` 派发即返 task_id + `on_done` 全局/每任务回调 + `snapshot()` 看板 + 完成态保留 200 条上限）+ `runtime_supervisor.delegate()`/`delegate_snapshot()`（校验唤醒 + 常驻池存在性，调度到常驻事件循环）；`CommandAgent.run` 改 `asyncio.to_thread(subprocess.run)` 修复「外部 agent 同步子进程卡死 `chuan-event-loop`、`asyncio.gather` 并行退化成串行」的 bug。CLI 新命令 `/bg <agent> <任务>` 后台派发、`/tasks` 看板，任务完成自动 print + HUD 推送。`tests/test_agent_harness.py`（8 例）覆盖派发即返/非阻塞/并发/回调隔离/未知 agent 优雅失败，全量 312 passed、2 skipped。
- **N21 状态机升级（补充）**：AgentHarness 升级为**任务状态机 + 依赖 DAG**（借鉴 dsh-agent-teams / NVIDIA AVO）：状态 `pending → ready → running → done/failed`；`submit(depends_on=[...])` 支持依赖编排，任务结束后 `_promote_pending()` 自动推进依赖就绪的下游任务（A→B→C 链式串联）；`_schedule()` 原子认领（ready→running 只一次，`claimed_by` 防重复执行）；depends_on 必须引用已存在任务（否则 ValueError），环天然不可达；依赖失败不阻断下游。TUI `/tasks` 看板新增 `⏳ pending / 🟡 ready / 🟢 done / 🔴 failed` + 依赖数，`delegate()` 透传 `depends_on`。测试新增 6 例，全量 321 passed、2 skipped。
- `chuan/tools.py` 保留为兼容占位（docstring 说明迁移去向），实际内置工具已迁移到 `chuan/tools/builtin_tools.py`。
- **N23 已完成**：P1 监督者全监控（ADR-018，借鉴 NVIDIA AVO Supervisor「CEO 只看轨迹、不干活」）落地。`chuan/gateway/supervisor_monitor.py`（`SupervisorMonitor`：`start_trace`/`record_step`/`check_dead_end`/`snapshot`，阈值可注入；死胡同三类确定性判定——循环 2-gram 覆盖 ≥0.95、反复失败 ≥0.7 相似或尝试耗尽、停滞 watchdog 超时，单步信号不足不误判）+ `role.py`（`PersonaRole` 注入 monitor，`_begin_trace`/`_finish_trace`/`_record_step`/`_check_dead_end`，重试前查死胡同并应用 redirect：abort / switch_agent / inject_hint，全旁路 try/except 不阻断主流程）+ `agent_spawner.py`（spawn 注入）+ `runtime_supervisor.py`（`monitor_status()`）+ TUI `/monitor` 面板 + 状态栏指示 + CLI `/monitor`。`tests/test_supervisor_monitor.py`（19 例）覆盖轨迹生命周期/裁剪/三类死胡同/redirect 决策/相似度边界/无 monitor 短路，全量 348 passed、2 skipped。
- **N24 已完成**：P1 Wiki 知识库（ADR-019，借鉴 Karpathy LLM Wiki / obsidian-second-brain / Aivy）落地。`chuan/wiki.py`（`Wiki`：`write` 实体归并——同名页合并更新、同名小节覆盖旧声明并折叠 `> 旧结论（deprecated）` 留痕、`import_source` 对 `sources/` raw 只读层只追加不覆盖、`search_index` index.md 索引定位、`reconcile`/`lint` 确定性健康检查，`_safe_slug` 支持中文实体名）+ `memory.py`（`remember`/`_with_frontmatter` 增 `confidence` 1-5）+ `memory_tools.py`（`build_wiki_tools`：`wiki_write`/`wiki_search`，无命中回退 `recall()`）+ `persona_loader.py`（全角色注入 wiki 工具）+ `consolidation.py`（`consolidate_sessions` 增 `wiki` 参数，蒸馏产物落 `sources/` raw 层而非 `notes/session-*.md`）+ `gateway/memory_ops.py`（`run_wiki_maintenance`/`kickoff_wiki_maintenance`：启动建 5 类目录 + lint + 每日维护 daemon 线程）+ `runtime_supervisor.py`（`wiki_status` 状态 + 启动挂接）。测试：`tests/test_wiki.py` 12 例（实体归并/raw 只读/index+log/reconcile/lint/工具暴露/蒸馏落点），全量 366 passed、2 skipped。
- **N27 已完成**：P2 知识原子自动沉淀（ADR-022，借鉴 Claude Code Auto Memory 后台提取）落地。`chuan/howto.py`（`HowToStore` 增 staging 待确认队列：`stage`/`staging_list`/`staging_get`/`approve(rename)`/`discard`，目录 `data/memory/howto_staging/` vault 外避免污染 FTS/wiki）+ `chuan/howto_distill.py`（`HowToDistiller`：`maybe_distill` 确定性门槛——失败/任务<8 字/结果<40 字/已有强命中原子(suggest≥10)/队列满 30/重复任务跳过；`_refine` LLM 可选润色 + 确定性回退：剥前缀取名、任务作触发场景、成功结果作怎么做）+ `role.py`（`PersonaRole._wrap_result` 收尾挂接 `_maybe_distill_howto`，显式/单 agent/规划汇总三路径全旁路，异常不阻断）+ `runtime_supervisor.py`（`howto_staging/howto_show/howto_approve/howto_discard` + `dispatch` 主流程集成：确认/否决消息前置路由 + 沉淀后追加「[待确认]」提示）+ `main.py`（`/howto` 命令 show/approve/discard + help）+ `tui/bridge.py` + `tui/app.py`（`/howto` 待确认队列面板，show/approve/discard 后重绘 + `howto_confirm` 路由标签）。测试：`tests/test_howto_distill.py` 28 例（门槛/提炼/确认/角色挂接/主流程确认与追加提示/按名大小写不敏感）+ `tests/test_tui.py` 3 例，全量 418 passed、2 skipped。
- **N28 已完成**：P2 例行自动化闭环（ADR-023，真用户故事「每周五自动出部署周报」）落地。`chuan/scheduler.py`（`ScheduledJob.weekly` 字段 + `_next_weekly` 下次时刻计算 + `add_weekly_job` + 触发后按周重排 + `on_routine_done` 完成回调）+ `chuan/routines.py`（`RoutineManager`：`Routine{name,message,schedule,agent,archive_to_wiki}`、`data/routines.json` 磁盘真相持久化、`parse_schedule` 支持 `fri 17:30`/`fri@17:30`/`every 3600`/`every@3600`、`apply_to` 注册进调度器并启动线程）+ `runtime_supervisor.py`（`routines` 管理器 + `routine_add/list/remove` + `_archive_routine_result` 归档 wiki sources/ 钩子 + `wake_up` 应用例行）+ `main.py`（`/routine` list/add/remove + help）+ `tui/bridge.py` + `tui/app.py`（`/routine` 自转看板）。测试：`tests/test_routines.py` 13 例（调度解析/每周计算/周任务触发重排/routine 增删查持久化/apply_to/归档钩子/管理接口）+ `tests/test_tui.py` 2 例（bridge 转发 + 面板渲染），全量 433 passed、2 skipped。
- **N29 已完成**：P2 例行任务失败重试（ADR-024，scheduler 级退避重试安全网）落地。`chuan/scheduler.py`（`ScheduledJob` 增 `retries/fail_count/retry_base/retry_factor/retry_max` + `_retry_backoff` 指数退避封顶 + `_run_job` 失败分级——仅 `KeyError` worker 缺失判永久不重试，其余异常 + 退化内容（空回复/`[PROACTIVE JOB ERROR]`/`[PROACTIVE JOB COMPLETED]` 占位符）瞬态可重试——返回 `ProactiveAlert|None` 静默退避 + `_is_failed_content` 确定性退化判定 + `add_interval/weekly_job` 增 `retries` 参数）+ `chuan/routines.py`（`Routine.retries` 持久化 `data/routines.json` + `apply_to` 透传 + `retry_state`）+ `chuan/runtime_supervisor.py`（`routine_add(..., retries)` + `routine_list` 暴露 `retries/fail_count`）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/routine add ... --retries N` + 面板 `🔁 retry #fc/rt` 状态）。测试：`tests/test_routines.py` 新增 8 例（退避时序/成功清零/耗尽告警/永久不重试/retries=0 兼容/空结果可重试/持久化透传/管理接口暴露），全量 441 passed、2 skipped。
- **N30 已完成**：P2 自动技能创建（ADR-025，技能即记忆，L3 闭环收尾）落地。`chuan/adapters/skill_loader.py`（`Skill` 增 `prompt/matches/render_prompt` + `SkillRegistry.add` 运行时注册 + `find_prompt_skill` 触发匹配）+ `chuan/skill_creator.py`（`SkillCreator`：maybe_create 确定性门槛 + `_derive_name/description/keywords/prompt` 纯确定性提炼 + staging 队列 `data/memory/skill_staging/` + approve 写 `skills/<name>.yaml`（`yaml.safe_dump`）+ 运行时注册）+ `chuan/role.py`（`_inject_reference` **技能触发词命中优先 → howto FTS 兜底** + `_maybe_inject_skill` 每次现读 skills/ 保证同会话即时 + `_maybe_create_skill` 挂接 `_wrap_result`）+ `chuan/runtime_supervisor.py`（`skill_creator` + `skill_staging/show/approve/discard/status`）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/skill` 面板：已注册 prompt 技能数 + 待确认队列）。测试：`tests/test_skill_creator.py` 12 例（关键词/门槛/staging/approve 写 YAML+注册/rename/discard/matches/find_prompt_skill/角色注入安全路径/监督接口），全量 453 passed、2 skipped。
- **N31 已完成**：P2 记忆「类型 + 硬容量」约束（ADR-026，借鉴 CC 4 类 + Hermes 2200 封顶）落地。`chuan/memory.py`（`MEMORY_TYPES` 4 类：fact/preference/process/memory + `_MAX_DOC_CHARS=2200`；`remember(..., type)` 确定性校验（非法归 memory）+ 单条硬截断（覆盖写同样遵守）；`_with_frontmatter(..., type)` 写 frontmatter；`recall(..., type)` 类型过滤；`MemoryHit.type` + `_recall_root` 附类型）+ `chuan/memory_tools.py`（`remember_memory/recall_memory` 增 `type` 参数与工具描述）。测试：`tests/test_memory.py` 新增 6 例（type 写入/默认/非法回退/2200 截断/recall type 过滤/工具透传），全量 459 passed、2 skipped。
- **N32 已完成**：P2 Mission 长任务追踪（ADR-027，借鉴 Aivy）落地。`chuan/mission.py`（`MissionManager` + `Mission{name,goal,agent,status,progress,task_ids,source}` + `data/missions.json` 磁盘真相持久化 + start/get/list/update/finish/pause/resume/remove，状态机 active→paused/done/failed）+ `chuan/gateway/agent_harness.py`（`submit(..., mission)` 透传）+ `chuan/runtime_supervisor.py`（`missions` 管理器 + `_on_harness_done` 后台任务完成自动回写进度/task_ids（不自动终结）+ `mission_start/list/finish/pause/resume/remove` + `delegate(..., mission)`）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/mission` 看板：🟢/⏸/✅/🔴 + 关联任务数 + 最近进度；`/bg --mission` 关联）。测试：`tests/test_mission.py` 8 例（CRUD/持久化/校验/update/finish-pause-resume-remove/harness 透传/完成回写/管理接口），全量 467 passed、2 skipped。
- **N33 已完成**：P1 ACI 预判注入（ADR-028，借鉴 BaiLongma）落地。`chuan/aci.py`（`AciPrefetcher`：`ThreadPoolExecutor` 并行预取**普通长期记忆**（FTS5 token 级 + `min_score` 阈值滤噪声）与 **wiki 实体页**（限定 notes/topics/entities/analysis/projects/howto 五目录，与 memory 源互斥）+ `render` 注入块 `【预判上下文】…` + `stats` 面板）+ `chuan/runtime_supervisor.py`（`_aci_prefetch_block`/`aci_status` + `dispatch`/`dispatch_async` 路由前预取 + `dispatch_to`/`_dispatch_chief(_async)` 透传 `aci_context`）+ `chuan/role.py`（`PersonaRole.dispatch(..., aci_context)` 前置注入任务文本，仅本岗位单次生效）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/aci` 面板：最近预取记忆/wiki 命中数 + 是否注入）。测试：`tests/test_aci.py` 14 例（预取命中/互斥/空库/阈值/旁路隔离/render/stats/岗位透传/管理接口）+ `tests/test_tui.py` 2 例（bridge 转发 + `/aci` 面板渲染），全量 483 passed、2 skipped。
- **N34 已完成**：P1 HUD 通道升级 SCENE 协议（ADR-029，借鉴 BaiLongma SCENE-PROTOCOL v1）落地。`chuan/channels/hud.py`（`SCENE_VERSION`/`SCENE_CAPS` + `_new_scene`/`scene_snapshot`/`_set_scene` + `send_hello`/`send_scene_full`/`send_patch`/`_dump` + 高层命令 SCENE 双发 + `hud.scene` 开关）+ `config/config.yaml`（`hud.scene: true`）+ `chuan/main.py` + `chuan/voice/main.py`（在线握手：`send_hello` + `send_scene_full`）+ `hud_overlay/lib/tcp_server.dart`（`TcpFrame`/`onFrame`/`reply` SCENE 帧分派）+ `hud_overlay/lib/agent_overlay.dart`（`_scene` 状态 + `_handleSceneFrame` hello→welcome + `_applyScene` 投影）。测试：`tests/test_hud.py` 新增 7 例（hello 携带 version/caps、scene 全量、snapshot 深拷贝、patch 不重发未变值、send_patch 空/离线降级、legacy 兼容 + scene:false 退回单发），全量 490 passed、2 skipped。Dart 端改动已人工审查（import dart:convert 已具备、Map 强转语法正确）；`flutter analyze` 在本沙箱受限（无法写 `%LOCALAPPDATA%/.dartServer` 缓存），需在有 Flutter 的环境跑一次 analyze 并重编译 assistant_overlay.exe 后再联调验证。
- **N35 已完成**：P2 任务断点续跑（ADR-030，借鉴 Aivy「流式打断不丢工具」）落地。`chuan/gateway/task_resume.py`（`RoleTaskResumeStore`：save_plan/save_result/resume_plan/list_resumable/clear，`data/task_resume/<session>.json` 磁盘真相 + content 截断 4000 + session 白名单 + 全旁路）+ `chuan/role.py`（`PersonaRole.__init__(..., resume_store)` + `dispatch(..., resume)` + `_dispatch_inner` resume 分支复用缓存 plan + `_rehydrate_plan` + `_execute(..., resume_hits)` 跳过已完成子任务 + `_run_subtask(..., resume_hits)` 命中直接 `[续跑复用]` 跳过 agent + 完成即存结果）+ `chuan/runtime_supervisor.py`（`resume_store` 实例 + `resume_to/list/clear`）+ `chuan/gateway/agent_spawner.py`（注入 resume_store）+ `chuan/main.py` + `chuan/tui/bridge.py` + `chuan/tui/app.py`（`/resume` 面板与命令）。测试：`tests/test_task_resume.py` 9 例（store 持久化/进度统计/清除/截断/rehydrate/复用跳过 agent/全量执行/新结果回写）+ `tests/test_tui.py` 2 例（bridge 转发 + `/resume` 面板渲染），全量 503 passed、2 skipped。
- **N36 已完成**：P2 外接知识库检索工具（ADR-031）落地。`chuan/memory_tools.py`（`build_vault_tools`：`list_vaults` 列库 + `search_vault(query, vault="", limit=5)` 检索外接库，`namespaces=[]` **绝不混入内部 notes/** + `vaults=[]`=检索全部外接库）+ `chuan/memory.py`（`recall` 外部库块改 `vaults is not None`——None=不查外接库（内部 recall_memory 默认）/空列表=全部外接库/列表=指定库；`_resolve_external_vaults` 懒加载 + `reindex_external` 增量索引独立 vault key，无 frontmatter 按 importance=3，只读）+ `chuan/persona_loader.py`（所有角色注入 build_vault_tools）+ `config/config.yaml`（`memory.external_vaults` 示例 obsidian → `D:/Resources/Obsidian`）。测试：`tests/test_vault_tools.py` 8 例（外部命中/空库名=全部/无命中提示/不混入内部记忆/未知库提示/列库/未配置提示/工具描述），全量 511 passed、2 skipped。
- **N37 已完成**：P2 岗位化 1:N 过渡（ADR-032，第一台阶：岗位多 agent 池 + 会话级状态隔离）落地。`chuan/role.py`（`PersonaRole._agents: dict[instance_id, agent]`，默认实例 id="default" 懒加载向后兼容 1:1 + `spawn_agent(instance_id, system_prompt, tools, model)` 显式扩容（同 id 幂等、无模型回退默认实例不抛错）+ `agent_count()`/`list_agents()` 暴露 N 实例；`_session_progress` + `_session_progress_view(session_id)` 进度按会话隔离，role 级 `self.progress` 保留最近会话视图兼容；`_state_writers: dict[session_id, TeamStateWriter]` 团队落盘按会话隔离，各写各的 `<session_id>.json`——同一岗位并行服务多会话互不串扰）+ `chuan/gateway/heartbeat.py`（健康报告新增 `role_agents` 汇总 + 摘要 `agent N`）。测试：`tests/test_role.py` 新增 7 例（默认实例入池/扩容到 N/同 id 幂等/无模型兜底/default+扩容共存/会话进度隔离/并发会话不串扰+团队落盘按会话）+ `tests/test_gateway_components.py` 新增 1 例（role_agents 汇总），全量 519 passed、2 skipped。
- **N38 已完成**：P2 岗位化 1:N 过渡·第二台阶（ADR-033，1:N 默认启用：并行子任务独立 worker）落地。`chuan/agent_pool.py`（`spawn_builtin_instance(persona_name, checkpointer)`：`persona_loader.birth(force_rebirth=True)` 绕过缓存拿**全新独立图**，同人设同工具但图互不共享）+ `chuan/role.py`（`_execute` 每波 `_assign_wave_instances(ready)`——本波 ≥2 个 auto/builtin 且无 specialist 的并行子任务各配一个 worker，上限 `CHUAN_PARALLEL_WORKERS` 默认 3 超出轮转复用；`_run_subtask(..., instance)` 用预分配实例否则 `_resolve_sub_agent`；`_ensure_worker_instance` 按需建 worker，池无能力/失败回退默认实例；`_resolve_sub_agent` 新增岗位实例 id 解析（specialist > 岗位实例 id > 常驻池 > 默认）——单任务/串行/指定实例/常驻 agent 保持原调度向后兼容）。测试：`tests/test_role.py` 新增 5 例（并行 auto 子任务各分独立 worker/`CHUAN_PARALLEL_WORKERS` 封顶复用/串行子任务仍走默认/`_resolve_sub_agent` 用岗位实例 + 未知名回退/`spawn_builtin_instance` force_rebirth 独立图），全量 524 passed、2 skipped。
- **N39 已完成**：P2 岗位化 1:N 过渡·第三台阶（ADR-034，按实例配置工具/模型/记忆）落地。`chuan/role.py`（`RoleAgentConfig{tools,model,system_prompt,checkpointer}` dataclass + `spawn_agent(instance_id, ..., config=None, checkpointer=None)` 合并 config（旧关键字参数可覆盖）+ `_agent_configs` 记录实例配置供检视 + `_worker_config` 岗位级并行 worker 默认配置 + `_ensure_worker_instance` 尊重配置（无能力/失败回退默认实例））+ `chuan/persona_loader.py`（`birth(model/tools/system_prompt)` 覆盖参数——model 覆盖跳过 brain 解析、tools 覆盖**精确替换** persona 工具集（不再自动加 sub_agent 工具）、system_prompt 覆盖人设）+ `chuan/agent_pool.py`（`spawn_builtin` 加 `checkpointer` + `spawn_builtin_instance` 覆盖透传）。测试：`tests/test_role.py` 新增 3 例（spawn_agent 应用 config 工具/模型/记忆/旧参数覆盖 config/worker 尊重岗位配置）+ `tests/test_persona_loader.py` 新增 1 例（birth 覆盖模型/工具/提示词/checkpointer），全量 528 passed、2 skipped。
- **N40 已完成**：P2 按任务复杂度选实例·声明式（ADR-035，config.yaml role_instances）落地。`chuan/role_config.py`（`RoleInstanceConfig{tiers,roles,instances}` + `load_role_instances(config_path, brains)` 解析器——`brain` 名用 brains registry 解析为模型（取不到保持 model=None）+ `tier_for(role)` 角色覆盖优先合并，缺段/缺文件/解析失败 → 全默认旁路）+ `chuan/role.py`（`PersonaRole(..., instance_config)` + `_classify_complexity`（纯规则：heavy=命中重型标记/medium=会规划/simple=其余）+ `_resolve_tier_instance` + `_ensure_configured_instance`（声明式实例经 spawn_builtin_instance 创建/复用，缺失/失败回退默认）+ dispatch 单 agent 路径按复杂度选实例）+ `chuan/gateway/agent_spawner.py`（从 sup.config_path+sup.brains 加载并注入所有岗位与幕僚长）+ `config/config.yaml`（`role_instances` 段，opt-in 示例：tiers/instances/roles）。测试：`tests/test_role_config.py` 新增 12 例（缺段/缺文件默认/解析 tiers+instances+roles/brain 解析/角色覆盖/未知 brain 回退/复杂度分级/选声明式实例/无配置回退/缺失实例回退/dispatch 重型用声明实例/simple 用默认/同 id 复用），全量 540 passed、2 skipped。
- **N47 已完成**：HTTP API / FastAPI Gateway（ADR-042，客户端/服务器解耦接入层）落地。`chuan/gateway/api.py`（`create_app` 工厂 + lifespan 生命周期（复用 supervisor 或自动 wake_up/shutdown）+ `GET /health` 复用 Heartbeat + `POST /api/chat` 走 dispatch（session_id 隔离 + 可选 history + 可选 worker 直派）+ 从简鉴权（`CHUAN_API_TOKEN` 或 config `api.token`，未设默认放行）+ 模块级 `app` 供 uvicorn/`python -m chuan.gateway.api` 启动）。测试：`tests/test_api.py`（8 例：health ok/未唤醒 degraded/chat 返回 reply+route/worker 直派/空消息 422/未唤醒 503/token 鉴权/默认放行）。实测 uvicorn 验收：`/health` → `{"status":"ok","awake":true,...13 workers}` ✓；`/api/chat {"message":"你好","session_id":"api_acceptance"}` → 幕僚长真实答复 ✓。全量 624 passed、2 skipped。
- **N49 已完成**：P2 vault MCP server（ADR-044）落地。`mcp_servers/vault_server.py`（FastMCP 自包含 stdio server，不依赖 chuan 包，对齐 filesystem_server）：`list_vaults` 列黑板/团队 + `search_vault` 检索（role/task/subtasks/notes 关键词命中 + 片段）+ `write_vault` 追加写入（不存在则新建，兼容既有 team_state 文档）；团队名白名单清洗 + realpath 前缀校验（限定 data/teams + data/memory）双保险防路径穿越 + 原子落盘；`config/mcp_servers.yaml` 追加 `vault` 段（未动其他 server 段）。测试：`tests/test_vault_server.py` 13 例（list/write/search/路径安全/工具注册），全量 640 passed、2 skipped。
- **N51 已完成**：P3 工具市场 / 动态能力发现（ADR-046，借鉴 BaiLongma）落地。`chuan/tool_market.py`（`ToolMarket`：`catalog()` 目录（来源 skill/mcp:*/extra + 上下架态）+ `enable()/disable()` 运行时上下架（`_collect()` 懒加载目录后校验未知工具返回 False）+ `enabled_tools()`（市场关闭 = 全量挂载对齐 ADR-009）+ `select(task, min_tools)` 确定性按信号裁剪——词元交集计分（**CJK 逐字拆分**让中文子串可命中、英文/数字整词）、不足 min_tools 回退全量防饿死、always 名单强制保留；`_tokenize`/`load_tool_market_cfg` 读 config.yaml `tool_market` 段）+ `config/config.yaml`（`tool_market` 段，`enabled: false` 默认关闭）+ `chuan/agent_pool.py`（`AgentPool.__init__(..., tool_filter)` + `spawn_builtin` 市场开启时对默认工具集过滤，失败回退全量不阻断 spawn）+ `chuan/runtime_supervisor.py`（构建 `tool_market`，开启时挂 `tool_filter`，暴露 `tool_market_status`/`tool_market_select`）+ `chuan/gateway/heartbeat.py`（健康报告 `market` 段）+ `chuan/main.py`（`/tools` 命令：查看目录 / enable / disable / select <任务> 预览裁剪 / refresh）。测试：`tests/test_tool_market.py` 9 例（目录来源/关闭态全量/上下架/未知工具/按描述计分裁剪/不足回退全量/下架+always/统计形状/config 默认关闭/中文逐字词元），全量 657 passed、2 skipped。
- **N52 已完成**：P3 视觉理解 V2（ADR-047，N50 扩展）落地。`skills/handlers/vision_analyze.py`（新增 `_ffmpeg_bin`/`_video_first_frame`（视频/录屏 ffmpeg 抽首帧，对齐 voice/tts.py ffmpeg 惯例）+ `_pdf_to_img`（pdf2image 转首页，缺依赖可读提示）+ `_table_to_img`（csv 用 Pillow 渲染表格图（xlsx 留提示），缺依赖可读提示）+ `vision_analyze` 经 `_resource_to_data_uri` 按 `_PDF_SUFFIXES/_TABLE_SUFFIXES/_VIDEO_SUFFIXES` 分派；空输入/文件不存在/缺 key/转换失败/模型失败全部静默降级，保持原文案契约）+ `skills/vision_analyze.yaml`（描述与触发词扩展到看 PDF/读表格/看视频/录屏/抽帧）。测试：`tests/test_vision_v2.py` 11 例（触发词扩展/无输入/图片仍走原 data URI/csv 渲染出图/csv 走模型/xlsx 留提示/csv 缺 Pillow 降级/PDF 缺依赖降级/视频抽帧 mock 走模型/缺 ffmpeg 降级/抽帧失败降级），`tests/test_vision_analyze.py` 8 例回归不破，全量 680 passed、2 skipped。
- **N54 已完成**：P4 声纹防欺骗（ADR-049，自研 voice）落地。`chuan/voice/spoof.py`（`extract_features`（numpy 规则：RMS 能量轮廓 PROFILE_POINTS=32 向量 + rms/zcr/静音占比/时长）+ `enroll_speaker`（原子落盘 data/speakers/<name>.json，防路径穿越 `_safe_name`，过短/全静音拒入）+ `load_speaker`/`list_speakers`/`remove_speaker`（磁盘真相）+ `anti_spoof`（两级：① 回放/环境噪声规则——静音占比过高/过短/能量过低判 spoof；② 已注册声纹 `_compare_voiceprint` 能量轮廓相关+标量贴近加权打分，低于阈值判伪造；未注册旁路通过）；float32(-1..1) 域计算，int16 输入自动 ×1/32768（对齐 wake_word.py 教训）；全程静默降级返回 dict 不抛错）。测试：`tests/test_voice_spoof.py` 13 例（enroll 写盘读回/拒静音过短/路径穿越/特征形状/静音判 spoof/过短判 spoof/未注册旁路/匹配通过/异声纹判伪造/未知名旁路/list+remove/int16-float32 缩放/garbage 不抛），`tests/test_voice.py` 46 例回归不破，全量 680 passed、2 skipped。
- **N55 已完成**：P3 向量 RAG 评估闸门（ADR-050，2026-08-24 RAG 可行性评估落地）完成。`skills/handlers/rag_gate.py`（`_count_md` 统计 .md 篇数/字符 + `_resolve_external_vaults` 读 config external_vaults + `_count_cases`/`record_missed_case` 漏召回案例留痕 `data/memory/vault/rag_missed_cases.md` + `rag_gate` 三态判定：未触发 / 规模达标待案例 / 触发（输出 embedding+faiss 评估清单）；确定性、失败静默降级）+ `skills/rag_gate.yaml`（type handler，触发词：向量评估/RAG 评估/库多大/漏召回/faiss）。测试：`tests/test_rag_gate.py` 11 例（注册/触发词/空库小库未触发/达标无案例待触发/有案例触发/外接库并入/案例追加计数/缺失目录降级/默认库不抛），全量 692 passed、2 skipped。
- **N56 已完成**：P4 媒体生成（ADR-052，自研）落地。`skills/handlers/media_gen.py`（`_tone`（正弦+指数包络）/`_synth_music`（C 大调或 A 小调和弦琶音，prompt 情绪词确定性映射 bpm：欢快=150/悲伤=70/缺省=110）/`_write_wav`（标准库 wave 写 16-bit PCM 44100Hz）/`_out_dir`/`media_generate`（kind=music 真合成落 wav；video/image 后端占位提示待接 seedance/seedream；未知类型/异常静默降级））+ `skills/media_gen.yaml`（type handler，触发词：生成音乐/配乐/做首歌/生成视频/生成图片/bgm）。测试：`tests/test_media_gen.py` 10 例（skill 注册/触发词/音乐写合法 wav 读回/输出目录自动建/悲伤比欢快长/确定性/视频占位/图片占位/未知类型降级/默认目录不抛），全量 702 passed、2 skipped。

## 已知遗留

- **dsh 融合 + 界面形态（ADR-051，2026-08-24）**：不学 dsh 起 agent 工作台端口，维持「core 持 scene + 多投影」现状（TUI / HUD / PWA 手机 / 微信 / 语音，本地+远程+移动+免提已全覆盖）；工作台 UI 不自己造（对齐编排层定位，ADR-007）。**触发条件**：dsh 正式版（API 稳定）发布 → 评估路径 B（把 chuan 网关/vault MCP 包装成 dsh 插件）与路径 C（借 dsh 的 UI 当工作台投影）。
- ~~原 9 个旧 persona 格式不一致待回填~~ → **N3 已用双格式兼容解决**：`PersonaLoader` 自动识别 legacy allowlist 和 ADR-009 deny 两种写法，两者都能正常出生，无需强制回填。若日后想统一为 ADR-009 格式仍可回填，但非阻塞项。
- 旧 persona 缺 `role` 字段时，`role_map()` 会退化为用 persona 名当 role（如 `lawyer→lawyer`），N4 路由可用但语义不如显式 role 清晰。
- API 备忘：`langgraph-checkpoint` 4.2.0 已无 `MemorySaver`，改用 `from langgraph.checkpoint.memory import InMemorySaver`（N5/N6 用记忆存档时注意）。

### N10 后新增遗留（2026-08 架构升级后）

> **已全部解决（2026-08-23 核对）**：下列 P0/P1 项已在 N11/N12 中落地，当前全量 257 passed、2 skipped，`wake_up()` 无启动报错。保留原文作为决策轨迹。

- ~~启动报错（P0）~~ → **已解决**：`_create_call_agent_tool()` 残留调用已随 Gateway 重构删除（`runtime_supervisor.py` 已无此方法，`wake_up()` 走 `agent_spawner.spawn()`）。
- ~~programmer 大脑 429（P0）~~ → **已解决**：persona 均不显式绑定 `cloud_coding`；路由大脑走 `routing.default_brain: bailian_flash` + `fallback_brain: cloud_general`，绕开 glm-4-plus 余额问题。
- ~~MCP 工具全空壳（P1）~~ → **已解决**：`mcp_servers/filesystem_server.py`（read_file/write_file/list_dir/delete_file）、`weather_server.py`（get_weather）、`opencode_wrapper.py`（run_opencode）均已实现；`chuan/tools/builtin_tools.py` 另提供同名纯 `@tool` 兜底。
- ~~Skill handler 仅 TODO（P1）~~ → **已解决**：`skills/handlers/legal_scan.py`（6 条合同风险正则）、`bash_safe.py`（两层安全闸 + 超时）、`web_search.py` 均已实现。
- ~~sub_agent 调用链路未通（P1）~~ → **已解决**：`PersonaLoader._resolve_sub_agent_tools()` 为每个 `sub_agents` 项生成 `call_{id}` 工具并注入角色工具列表；`personas/programmer.yaml` 已声明 `sub_agents: [prime_agent, pi, claude_code, opencode]`。
- ~~会话持久化为内存版（P1）~~ → **已解决**：`Memory` 用 `AsyncSqliteSaver`（配合 aiosqlite）持久化，`SessionManager.setup_checkpointer()` 在常驻事件循环内初始化，重启不丢会话。
- **pi 已改本地直接调用**：N7 验收中提到的"Docker wrapper"已废弃，pi 改为 npm 全局安装 + subprocess 直接调用（ADR-010 Docker 选择性）。
- **智谱 API 适配**：brains.py 已支持 OpenAI 兼容协议；实际路由默认脑已切到百炼 qwen-flash（`bailian_flash`），智谱 glm-4-flash 作为兜底。

---

## N11–N23 节点计划（架构升级阶段）

> N0-N10 完成了基础班底骨架。N11 起进入"能干活→能记住→能说话→能进化"的架构升级阶段，对应 [DEVELOPMENT.md](../guide/DEVELOPMENT.md) 的 P0-P5 优先级。

| 节点 | 干什么 | 优先级 | 对应 ADR | 说明 |
|------|--------|--------|----------|------|
| **N11 修复启动+基础工具** | 修启动报错、programmer改brain、MCP filesystem实现、bash工具 | P0-P1 | - | 让项目能跑起来、能干活，**DONE** |
| **N12 会话持久化+sub_agent** | SqliteSaver、call_pi工具注入、pi作为programmer子agent | P1 | - | 重启不丢，编程活能调pi，**DONE** |
| **N13 三层记忆体系** | 短期会话(SqliteSaver)+长期检索(FTS5)+共享黑板(Obsidian)完善（向量语义=预留未实现，见下；现由 N43 sqlite-vec 旁路承担，本地 faiss/vector_store/rag_corpus **预留未实现**） | P2 | - | 借鉴 OpenClaw 三层记忆，**DONE** |
| **N14 SOUL.md 角色驱动** | persona YAML → 目录+SOUL.md/MEMORY.md/config.yaml 迁移，PersonaLoader双格式兼容 | P2 | ADR-013 | 借鉴 OpenClaw/Jarvis，agent可自写记忆，**DONE** |
| **N15 语音交互闭环** | OpenWakeWord唤醒+faster-whisper STT+edge-tts/piper TTS，全双工语音 | P3 | ADR-011 | 最终交互方式，后台常驻，**DONE** |
| **N16 角色独立音色+语音素材** | config/voices.yaml、事件音效（程序化合成）、多助手音色配置 | P3 | - | 借鉴 assistant-x-openclaw，**DONE** |
| **N17 TUI 终端界面** | Textual 实现（已完成）：水线 splash 门面、对话流+可折叠路由树、底部活跃角色条、角色专属色、chuan-tui 入口 | P3 | ADR-011 | 非最终界面，仅调试 |
| **N18 Gateway 组件拆分** | runtime_supervisor.py 拆分为 gateway/ 七大组件 | P4 | ADR-012 | 借鉴 OpenClaw Gateway，**DONE** |
| **N19 多角色并行+微信接入** | 复杂任务fan-out多角色并行、微信远程操控电脑 | P4-P5 | ADR-014/015 | 借鉴 OpenClaw 微信接入，**DONE** |
| **N20 自改进循环(GEPA)** | Generate-Execute-Preserve-Assess，干完活评估沉淀到 MEMORY.md | P5 | - | 借鉴 Jarvis 自改进，**DONE** |
| **N21 后台委派 harness** | fire-and-forget 委派外部 agent 黑盒跑长任务，派发即返 + 完成异步回推 | P4 | ADR-016 | 借鉴 deepseek-harness，**DONE** |
| **N22 MCP 管理面板** | TUI 可视化 MCP server 连接/工具/错误状态 + 单 server 运行时启停（不改 yaml） | P0 | ADR-017 | 借鉴 Aivy MCP 面板，**DONE** |
| **N23 监督者全监控** | 全程监控 worker 执行轨迹，死胡同检测（循环/反复失败/停滞）+ redirect（换 agent/注入思路/中止）+ **HUD 可视化**（监督者数据经 `monitor:{json}` 推送 Flutter 左下 SUPERVISOR 面板） | P1 | ADR-018 | 借鉴 NVIDIA AVO Supervisor「CEO 只看轨迹」，**DONE** |
| **N24 Wiki 知识库** | 在长期记忆上叠加结构化知识库：6 类目录（sources 只读 + topics/entities/analysis/projects/howto）、实体页改写（同名归并 + deprecated 留痕）、index.md/log.md 双文件（替代 RAG）、reconcile/lint 健康检查、consolidation 蒸馏落 sources/ + 归位 ingest（原料→实体页，LLM 路由/确定性回退 + 幂等） | P1 | ADR-019 | 借鉴 Karpathy LLM Wiki / obsidian-second-brain / Aivy，**DONE** |
| **N25 外接只读库接入 FTS5** | 外接 Obsidian 库（config `memory.external_vaults`）只读接入 FTS5：多 vault key 隔离（复用现有多 vault 表结构）、`reindex_external` 增量同步（mtime + 删除清理 + 跳过隐藏目录）、`recall(vaults=...)` 显式跨库召回且默认隔离、启动挂接 | P2 | ADR-020 | 向量 RAG 评估闸门的「更便宜先手」，**DONE** |
| **N26 L3 从做到造知识原子** | 可复用「怎么做」知识原子闭环（**已并入 wiki 第 6 类 `howto/`**）：HowToStore 委托 `Wiki.write`（白得 index/lint/双链/归并留痕）、`howto_save/find/show` 工具（全角色）、`PersonaRole._maybe_inject_howto` 开工前自动注入「参考做法」（阈值 10 防噪声）——重复做一件事 → 沉淀原子 → 下次自动复用 | P2 | ADR-021 | 借鉴姜胡说 7 层 L3（抖音《从零搭建AI知识库》），**DONE** |
| **N27 知识原子自动沉淀** | L3 闭环补「自动」：任务成功收尾时 `HowToDistiller` 自动提炼候选原子（确定性门槛：失败/过短/无实质/已有原子/队列满/重复任务跳过；LLM 可选润色 + 确定性回退）→ 入 staging 待人工确认队列（`data/memory/howto_staging/`，vault 外不污染 FTS/wiki）→ `/howto` show/approve/discard 确认后才经 `Wiki.write` 入库——知识库只被确认过的做法增长 | P2 | ADR-022 | 借鉴 Claude Code Auto Memory 后台提取，**DONE** |
| **N28 例行自动化闭环** | 例行任务（routine）一等概念：`RoutineManager`（`data/routines.json` 持久化）+ 每周调度（`add_weekly_job`，`fri 17:30` / `every 3600`）+ `PersonaRole` howto 注入与沉淀自动生效 + 可选 `archive_to_wiki` 归档 wiki sources/ 供 ingest 归位——「每周五自动出部署周报」从「沉淀成原子」升级为「被系统定期执行」，scheduler+howto+wiki 串成自转 | P2 | ADR-023 | 真用户故事：系统自转而非等召唤，**DONE** |
| **N29 例行失败重试** | 例行任务失败重试（任务级安全网）：失败分级（仅 `KeyError` worker 缺失永久不重试，其余异常+退化内容瞬态可重试）+ 指数退避（基数 60s/系数 2/封顶 30min，确定性无抖动）+ `retries` 可配（`--retries N`，0=关闭向后兼容）+ `fail_count` 本轮窗口累计成功后清零 + 重试间隙静默、耗尽才 ERROR 告警 + `/routine` 面板 `🔁 retry` 状态——与 N23 任务内死胡同分层，兜底整轮 `dispatch_to` 级别失败 | P2 | ADR-024 | 通用 backoff 重试，**DONE** |
| **N30 自动技能创建** | 技能即记忆（L3 闭环收尾）：任务成功收尾自动提炼**可注册 prompt 型技能**（`skills/<name>.yaml`，`type: prompt`，含 `trigger.keywords` + `prompt` 做法），复用 howto「自动提炼→人工确认」模式（staging `data/memory/skill_staging/` → `/skill` approve 写 YAML + `SkillRegistry.add` 运行时注册）；复用注入 `_inject_reference` **技能触发词命中优先 → howto FTS 兜底**；确定性门槛/提炼无 LLM，全旁路 | P2 | ADR-025 | 调研（Codex/Hermes 技能即记忆）+ 自研 `skill_creator`，**DONE** |
| **N31 记忆类型+硬容量** | 记忆「类型 + 硬容量」约束：`type` 字段（CC 4 类 fact/preference/process/memory，非法归 memory）+ 单条 `_MAX_DOC_CHARS=2200` 字符硬截断（Hermes 封顶）——`remember(..., type)` 写 frontmatter、`recall(..., type)` 过滤、`MemoryHit.type` 暴露、`remember/recall_memory` 工具透传——防止 N26-N30 自动沉淀让记忆失控膨胀 | P2 | ADR-026 | 调研（CC/Hermes），**DONE** |
| **N32 Mission 长任务追踪** | 跨对话长任务看板（借鉴 Aivy）：`MissionManager`（`data/missions.json` 持久化 + 状态机 active→paused/done/failed + start/get/list/update/finish/pause/resume/remove）+ `AgentHarness.submit(..., mission)` 透传 + `_on_harness_done` 后台任务完成自动回写进度（不自动终结）+ `delegate(..., mission)` + CLI/TUI `/mission` 看板与 `/bg --mission`——把一次委派升级为跨会话可追踪的长目标 | P2 | ADR-027 | Aivy Mission 长任务追踪，**DONE** |
| **N33 ACI 预判注入** | 路由前并行预取上下文（借鉴 BaiLongma）：`AciPrefetcher`（`ThreadPoolExecutor` 并行召回**普通长期记忆**（FTS5 token 级 + `min_score` 阈值滤噪声）与 **wiki 实体页**（限定 notes/topics/entities/analysis/projects/howto 五目录，与 memory 源互斥）+ `render` 注入块 `【预判上下文】…`）+ `dispatch`/`dispatch_async` 路由前 `_aci_prefetch_block` → `dispatch_to`/`_dispatch_chief(_async)` 透传 `aci_context` → `PersonaRole.dispatch(..., aci_context)` 前置注入任务文本 + CLI/TUI `/aci` 面板（最近预取记忆/wiki 命中数 + 是否注入）——agent 首轮直接带背景开工，减少首轮空转 | P1 | ADR-028 | BaiLongma 预判注入，**DONE** |
| **N34 HUD 通道升级 SCENE 协议** | core 持 scene → UI 纯投影（借鉴 BaiLongma SCENE-PROTOCOL v1）：`HudChannel` 维护结构化 scene 状态（version/agent/effect/user/ai/monitor/tool_call）+ `hello:{json}` 握手（client+version+caps 能力协商）→ 前端回 `welcome` + `scene:{json}` 全量（初始化/重连）+ `patch:{json}` 增量（只含变化字段，`_set_scene` 值未变不发）+ `hud.scene` 开关（默认 true：legacy + patch 双发兼容旧 Flutter；false 退回单发）+ 前端 `TcpFrame`/`onFrame`/`reply` 解析 + `_applyScene` 投影——为手机 PWA 复用同一协议铺路（TCP/WebSocket 只换传输层） | P1 | ADR-029 | BaiLongma SCENE-PROTOCOL，**DONE** |
| **N35 任务断点续跑** | 打断不丢工具（借鉴 Aivy「流式打断不丢工具」）：`RoleTaskResumeStore`（`data/task_resume/<session>.json` 磁盘真相 + save_plan 规划落定即存 + save_result 子任务完成即存（content 截断 4000）+ resume_plan/list_resumable/clear，session 白名单清洗 + 全旁路）+ `PersonaRole`（`dispatch(..., resume)` + `_rehydrate_plan` 复用缓存 plan 而非重新规划 + `_execute(..., resume_hits)` 跳过已完成子任务 + `_run_subtask` 命中缓存直接 `[续跑复用]` 返回跳过 agent + 完成即存结果）+ CLI/TUI `/resume <session> <worker>` 看板（progress total/done）——长任务被打断后复用已执行子任务结果，只重跑未完成部分 | P2 | ADR-030 | Aivy 流式打断不丢工具，**DONE** |
| **N36 外接知识库检索** | search_vault/list_vaults（临时查外置 Obsidian 库，与内部记忆隔离）：`build_vault_tools`（`list_vaults` 列库 + `search_vault(query, vault="", limit=5)`，vault 留空=全部外接库）+ `Memory.recall(..., namespaces=[], vaults=...)` 语义（None=不查外接库/[]=全部/列表=指定）+ 独立 vault key（`_vault_key_for`）+ `reindex_external` 增量索引（无 frontmatter importance=3，只读不写回）+ 所有角色注入——「临时查外置库」专用，不混入 `recall_memory`/ACI 预判的内部管道 | P2 | ADR-031 | 定位讨论，**DONE** |
| **N37 岗位化 1:N 过渡** | 岗位多 agent 池 + 会话级状态隔离（ADR-014「逐步迁移」第一台阶）：`PersonaRole._agents`（默认实例 id="default" 向后兼容 1:1 + `spawn_agent` 显式扩容（同 id 幂等/无模型回退默认）+ `agent_count`/`list_agents`）+ `_session_progress` 进度按会话隔离（role 级 `self.progress` 保留最近会话视图兼容）+ `_state_writers` 团队落盘按会话隔离 + heartbeat `role_agents` 可观测——同一岗位可并行服务多会话（微信/CLI/语音）互不串扰 | P2 | ADR-032 | 逐步迁移（第一台阶），**DONE** |
| **N38 岗位化 1:N·第二台阶** | 1:N 默认启用（ADR-033）：`AgentPool.spawn_builtin_instance`（`birth(force_rebirth=True)` 独立图）+ `_assign_wave_instances`（本波 ≥2 并行 auto 子任务各配独立 worker，上限 `CHUAN_PARALLEL_WORKERS` 默认 3）+ `_run_subtask(..., instance)` 透传 + `_ensure_worker_instance`（无能力/失败回退默认）+ `_resolve_sub_agent` 支持岗位实例 id（specialist > 岗位实例 > 常驻池 > 默认）——并行子任务不再挤同一默认实例，实例 id 可被子任务显式指定 | P2 | ADR-033 | 逐步迁移（第二台阶），**DONE** |
| **N39 岗位化 1:N·第三台阶** | 按实例配置工具/模型/记忆（ADR-034）：`RoleAgentConfig{tools,model,system_prompt,checkpointer}` 贯穿 `spawn_agent`（config + 旧参数可覆盖）与 worker（`_worker_config`）+ `birth(model/tools/system_prompt)` 覆盖参数（model 跳过 brain、tools 精确替换）+ `spawn_builtin` 加 `checkpointer` + `spawn_builtin_instance` 覆盖透传 + `_agent_configs` 记录实例配置——每个实例可配不同工具子集/模型/系统提示词/会话存档（记忆），「按任务复杂度选实例」落地 | P2 | ADR-034 | 逐步迁移（第三台阶），**DONE** |
| **N40 按复杂度选实例·声明式** | config.yaml `role_instances` 声明式配置（ADR-035）：`load_role_instances` 解析 `tiers`（simple/medium/heavy→实例 id，角色可覆盖）+ `instances`（brain/tools/system_prompt 声明，brain 用 registry 解析为模型）+ `roles.<name>.tiers` 覆盖 + `tier_for` 合并 + `_classify_complexity`（纯规则重型标记/规划/简单）+ `_resolve_tier_instance`/`_ensure_configured_instance`（声明式实例创建/复用/回退）+ dispatch 单 agent 路径按复杂度选实例——「简单→默认、重型→更强模型/编码实例」改配置即生效，全默认旁路兼容 1:1 | P2 | ADR-035 | 定位讨论，**DONE** |
| **N46 本地资源感知采集器** | 系统/桌面/SSH/Git 状态确定性采集 handler skill（ROADMAP P3「本地资源感知」落地）：`system_status`（CPU/内存/磁盘/主机，纯标准库 + ctypes `GlobalMemoryStatusEx`/`disk_usage`）、`desktop_status`（前台窗口标题 + 主屏分辨率，ctypes WinAPI）、`ssh_status`（~/.ssh/config 主机 + known_hosts + netstat 活跃 22 端口）、`git_status`（分支/改动/stash/最近提交，只读 git）；`skills/*.yaml`（type: handler + trigger）+ `skills/handlers/*.py`，失败静默降级 | P3 | ADR-041 | 确定性、无新依赖（psutil 未装）、不依赖 LLM；全量 skill 加载 + 触发词实测通过，**DONE** |

### 依赖关系

```
N11 → N12 → N13 → N14 → N15 → N16
              ↘ N17（可并行）
N18（可在 N14 后开始，渐进式）
N19 → N20
N21（可在 N20 后独立开始，复用常驻池 + 常驻事件循环）
N22（可在 N21 后独立开始，复用常驻事件循环 + MCPAdapter）
N23（可在 N21/N22 后独立开始，复用岗位调度重试链路 + 常驻池名单）
N24（可在 N13/N23 后独立开始，复用 Memory + consolidation 蒸馏链路 + 常驻事件循环）
```

**关键路径**：N11（能跑）→ N12（能调pi）→ N15（能说话）。N13/N14/N17 可穿插。

## N0 验收

- `python -m chuan.main` 输出 banner：`川流 chuan-os v0.1.0 / 幕僚长正在醒来… / （骨架搭建中，待实现交互循环）`
- 依赖版本：langgraph 1.2.11、langchain-core 1.6.0、langgraph-supervisor 0.0.31、langgraph-checkpoint 4.2.0、langchain-ollama 1.1.0、faiss-cpu 1.15.0 等。

## N1 验收

- `from chuan import BrainRegistry` 可用；`BrainRegistry().list()` 返回 `['cloud_general', 'cloud_coding', 'local']`。
- `registry.default()` 返回 `cloud_general`（`ChatOpenAI`），`registry.fallback()` 返回 `local`（`ChatOllama`）。
- `brain.complete("你好")` 和 `brain.complete([{"role":"user","content":"..."}], system="...")` 接口工作正常，内部正确转译为 LangChain 消息格式。
- 底层 model 对象（`ChatOpenAI`/`ChatOllama`）可直接喂给 LangGraph `create_react_agent`，无需额外包装。

## N2 验收

- `SkillRegistry().list_all()` 返回 `['code_execution', 'contract_review', 'weather_check']`。
- `contract_review`（handler 类型）成功转为 LangChain Tool，`func` 指向 `handlers.legal_scan.scan_contract`。
- `code_execution`/`weather_check`（mcp 类型）标记依赖 `opencode`/`weather` server，`list_mcp_dependencies()` 正确返回。
- `ToolRegistry.get_tools(deny=['contract_review'])` 返回空列表；`deny=['weather_check']` 返回 `['contract_review']`；ADR-009 减法模型工作正常。
- `MCPAdapter` 能读取 `mcp_servers.yaml` 配置，`connect_all()` 为每个 server 建立 `stdio_client` + `ClientSession` + `load_mcp_tools`；连接失败的 server 记录错误但不阻断其他。

## N3 验收（承重墙，已通过）

- 14 个 persona 全部加载；新旧格式正确识别（5 个 ADR-009 deny 格式 + 9 个 legacy allowlist 格式）。
- `birth('lawyer')` 返回真实 `CompiledStateGraph`，`isinstance` 检查通过，`.invoke` 存在，`.name == 'lawyer'`。
- **决定性验收 —— 真跑 `.invoke()`**：`agent.invoke({"messages":[{"role":"user","content":"帮我看份合同"}]})` 成功返回 2 条消息，末条为模型回复。注入的 system prompt 经验证为：`你是律师，chuan-os（川流）班底中的一员。\n职责：合同审查、法律咨询、风险提示\n只做自己职责范围内的事；超出范围的，交回幕僚长转派。`
- 工具解析双格式验证：`lawyer`（legacy allowlist）→ `['contract_review']`；`researcher`（ADR-009 deny）→ `['contract_review']`。
- 生命周期：缓存命中返回同一对象；`force_rebirth=True` 返回新对象；`kill()` 清除缓存（ADR-007 用完即焚）。
- `birth_all(exclude=['chief_of_staff'])` 成功出生 13 个 worker，幕僚长正确排除 —— N4 可直接消费。

## N4 验收（承重墙，已通过）

- [x] `RuntimeSupervisor().wake_up()` 成功出生 13 个 worker（排除 chief_of_staff）
- [x] `supervisor.shutdown()` 正确清理所有 worker
- [x] `Orchestrator().route("帮我看份合同")` 返回 `"lawyer"`（关键词匹配）
- [x] `Orchestrator().route("修复这个bug")` 返回 `"programmer"`（关键词匹配）
- [x] `Orchestrator().route("切到 programmer")` 返回 `"programmer"`（显式锁定）
- [x] `Orchestrator().route("今天天气不错")` 返回 `None`（需 LLM 兜底）
- **注意**：`dispatch()` 需要 API key 才能实际调用 LLM，当前仅验证了初始化和路由逻辑

## N5 验收（承重墙，已通过）

- [x] `Guard().review("agent", "rm -rf /")` 返回 REJECT
- [x] `Guard().review("agent", "echo hello")` 返回 APPROVE
- [x] `Guard().review_batch()` 批量审核工作正常（strict_mode 短路）
- [x] `guard.as_post_model_hook()` 可作为 post_model_hook 使用
- [x] `RuntimeSupervisor(guard=guard).wake_up()` 正常集成
- [x] 规则管理 `add_pattern()`/`remove_pattern()`/`list_patterns()` 工作正常

## N6 验收（已通过）

- [x] `Memory().remember("contract", "...")` 生成可直接被 Obsidian 打开的 `notes/contract.md`，`recall("合同风险")` 能按相关性召回。
- [x] 黑板核心空间为 `shared/`；`external_agent="example"` 的读写严格落在 `shared/external/example/`，不会覆盖核心条目。
- [x] 非法文档名和命名空间（如 `../escape`）抛出 `ValueError`，不允许写出 vault。
- [x] `RuntimeSupervisor` 已接入 LangGraph `InMemorySaver`；调用 `dispatch` / `dispatch_async` 可通过 `session_id` 保持独立会话。

## N7 验收（已通过）

- [x] `ExternalAgentLoader` 只加载 `external_agents.enabled` 中的名称；同目录未启用的 agent 不会被发现。
- [x] 外来 `agent.yaml` 的 prompt 直接并入 persona system prompt，能由 `PersonaLoader` 正常 birth 为 worker。
- [x] 声明式 `command` 被编译为可由 Supervisor 直接路由的 stdin/stdout worker；非零退出、超时与启动失败均以可读结果返回。
- [x] Tool 以 `shell=False` 执行，且危险输入（如 `rm -rf /`）在启动进程前被 Guard 拦截。
- [x] `agents/pi/` 已作为命令型 `pi` worker 显式启用；其 Docker wrapper 以受限容器运行 Pi，且不将宿主凭证目录挂入容器。

## N8 验收（已通过）

- [x] `ProactiveScheduler.add_interval_job()` 可新增定时管家任务，`run_pending()` 只执行到期任务。
- [x] 任务经 `dispatch_to()` 直接调用目标 worker，并使用 `proactive:<job>` 独立会话。
- [x] worker 返回的末条消息进入 `ProactiveAlert` 队列；读取时可选择清空。
- [x] worker 抛出的异常也转为错误提醒，且任务保持下次调度。
- [x] `scheduler.enabled` 默认 `false`；显式开启后由 `RuntimeSupervisor.wake_up()` 加载，关闭时自动停止。

## N9 验收（已通过）

- [x] `python -m chuan.main` 进入交互循环并唤醒幕僚长。
- [x] 普通消息通过 `RuntimeSupervisor.dispatch()` 进入完整路由链路。
- [x] `/workers`、`/alerts`、`/help` 与 `exit` 命令可用；请求异常展示为可读错误，不会终止会话。
- [x] Ctrl+C、EOF 与退出命令都会调用 `shutdown()`，避免遗留后台调度线程。

---

## 后续候选（N24+，未落地借鉴点）

> 均为「已调研、未开发」的**活跃待办**。来源一：[LEARNINGS-2026-08-23.md](../reference/LEARNINGS-2026-08-23.md)（竞品调研）；来源二：2026-08-24 记忆/知识库调研（Claude Code / Codex / OpenCode / Hermes / obsidian-second-brain）。已落地项标注 ✅ 不再列入活跃待办（Aivy 子智能体并行→N21、Aivy MCP 面板→N22、Wiki/实体页改写/知识库维护→N24）。

| 优先级 | 待办 | 借鉴来源 |
|---|---|---|
| P1 | ✅ Wiki 知识库：5 类目录 + 双向引用 + AI 主动整理裁决 → **N24/ADR-019** | Aivy |
| P1 | ✅ ACI 预判注入：路由前并行预取记忆+wiki 上下文注入岗位 → **N33/ADR-028** | BaiLongma |
| P1 | ✅ HUD 通道升级 SCENE 协议（为手机 PWA 铺路） → **N34/ADR-029** | BaiLongma |
| P1 | ✅ 长期记忆「实体页改写」：session 快照追加 → 按实体归并改写 → **N24 `Wiki.write`** | 调研（CC/Codex/OSB） |
| P1 | ✅ 知识库维护层：改写实体页 + 矛盾调和 + 过期替换 + index/log → **N24 `reconcile`/`lint`** | obsidian-second-brain |
| P2 | ✅ Mission 长任务追踪 + 跨对话看板 → **N32/ADR-027** | Aivy |
| P2 | ✅ 流式打断不丢工具（保留已执行结果，只重跑未完成） → **N35/ADR-030** | Aivy |
| P2 | ✅ 局域网 HTTPS + 手机 PWA 接入 → **N48/ADR-043** | BaiLongma |
| P2 | ✅ 记忆加「类型 + 硬容量」约束（学 CC 4 类 + Hermes 2200 字符封顶） → **N31/ADR-026** | 调研（CC/Hermes） |
| P2 | ✅ 技能即记忆：重复流程自动沉淀 SKILL.md → **N30/ADR-025**（prompt 型技能） | 调研（Codex/Hermes） |
| P2 | ✅ 自动技能创建落地：干完活自动创建新技能（`skill_creator.py`） → **N30/ADR-025** | 自研（REFERENCES L36） |
| P2 | ✅ 岗位化 1:N 过渡：多 agent 池+会话隔离 → **N37/ADR-032**；1:N 默认启用并行独立 worker → **N38/ADR-033**；按实例配置工具/模型/记忆 → **N39/ADR-034**；config.yaml 按复杂度选实例 → **N40/ADR-035**；动态实例池自动扩缩容 → **N41/ADR-036**；岗位间协作·多岗位并行编排+共享黑板 → **N42/ADR-037**；记忆语义检索·sqlite-vec 双路合并 → **N43/ADR-038**；Redis TTL 缓存旁路·cache-aside 加速 → **N44/ADR-039**；任务队列+事件总线·Streams 可靠队列+Pub/Sub 总线 → **N45/ADR-040** | ADR-014 |
| P2 | ✅ vault MCP server：外来 agent 经 MCP 检索/写入共享黑板 → **N49/ADR-044**（search_vault/write_vault/list_vaults 读写 data/teams/ 黑板） | obsidian-second-brain |
| P2 | ✅ search_vault 检索工具：临时查外置 Obsidian 库，不混入记忆管道 → **N36/ADR-031** | 定位讨论 |
| P3 | ✅ 视觉理解（图片/截图分析 V1 → **N50/ADR-045**；录屏/PDF/表格转图 V2 → **N52/ADR-047**） | Aivy |
| P3 | ✅ 工具市场 / 动态能力发现（运行时按信号裁剪工具集 → **N51/ADR-046**；默认关闭对齐 ADR-009，/tools 运行时上架下架 + select 确定性裁剪） | BaiLongma |
| P3 | 本地资源感知（系统/桌面/SSH/Git 采集器） → 完成（**N46/ADR-041**，2026-08-24）；天气采集已由既有 weather_check skill 覆盖 | BaiLongma |
| P3 | ✅ HTTP API / FastAPI Gateway（客户端/服务器解耦，接入层扩展） → **N47/ADR-042** | 自研（ADR-011） |
| P3 | ✅ 文档口径修正：向量语义召回过度宣称 → 标注「预留未实现」（faiss/vector_store/rag_corpus） → **N48/ADR-043** | 自审 |
| P3 | ✅ 向量 RAG 评估闸门：确定性量化记忆库规模 + 漏召回案例留痕 → **N55/ADR-050**（触发条件 = 合计 >1000 篇/100 万字符 **且** 有漏召回案例，才启动本地 embedding+faiss 评估；当前未触发，继续 FTS5；faiss 1.15 已装、缺 sentence-transformers/torch） | 自审（2026-08-24 RAG 可行性评估） |
| P4 | 机器绑定加密 / 陌生人距离 / 自动锁屏 | Aivy |
| P4 | ✅ 媒体生成（音乐程序化合成 V1 → **N56/ADR-052**；视频/图片后端 seedance/seedream 待接入，接口已留） | BaiLongma |
| P4 | ✅ 声纹防欺骗（anti_spoof + enroll_speaker，V1 规则版 → **N54/ADR-049**；重模型后端 pyannote/ecapa 留待扩展） | 自研（voice） |
| P4 | Electron 桌面壳 + 安装包 + 激活码（面向最终用户） | 两家 |
| P3 | GUI 自动化（借鉴影刀 RPA 能力，补「无 API 软件操作」这条腿） → **N57/ADR-054**（阶段1 截图+元素定位 mss→pywinauto 主/vision_analyze 视觉兜底；阶段2 pywinauto 元素操作主力+pyautogui 坐标兜底，**默认后台静默模式（UIA 不抢焦点）**；阶段3 gui_operate 闭环+安全闸 guard/超时/截图留痕；阶段4 测试+文档。可选增强 UI-TARS（视觉 GUI agent，MCP 接入），备选 RPA Framework。已列计划+选型，未开工） | 影刀 |
